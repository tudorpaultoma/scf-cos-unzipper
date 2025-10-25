"""
Tencent SCF COS Unzipper

Purpose:
- Serverless function that listens for COS PutObject events containing .zip files
- Downloads the zip to /tmp, safely extracts entries, and uploads extracted files back to COS
- Output layout: OUTPUT_PREFIX/<zip-base-name-without-ext>/...

Key behaviors:
- Fully URL-decodes and normalizes event object keys, handling leading "/appid/bucket/" formats
- Ignores folder-marker events and output prefix files to prevent infinite loops
- Prevents Zip Slip: rejects absolute paths and parent directory traversal
- Robust directory detection via ZipInfo.is_dir() and external_attr mode bits
- Parallel uploads using ThreadPoolExecutor; MAX_WORKERS configurable via env
- Content types assigned with Python mimetypes
- Recursively processes nested zip files up to MAX_RECURSION_DEPTH levels
- Extracts zip contents but does NOT upload the zip files themselves
- Cleans up temp zip after processing

Configuration via environment variables:
- COS_BUCKET: Target bucket. Prefer full form "<bucket>-<appid>"; code can infer from event if short
- INPUT_PREFIX: Prefix to watch for zip uploads (e.g., "unzipper-input/")
- OUTPUT_PREFIX: Prefix to write extracted files (e.g., "unzipper-output/")
- REGION or TENCENTCLOUD_REGION: COS region (default "ap-guangzhou")
- TENCENTCLOUD_SECRETID/SECRETKEY/SESSIONTOKEN: Credentials injected by SCF role, or use SECRETID/SECRETKEY/SESSIONTOKEN
- MAX_WORKERS: Optional, number of parallel uploads (default 16)
- MAX_RECURSION_DEPTH: Maximum depth for recursive zip processing (default 10)

Returns:
- On success: {"status":"ok","bucket":<bucket>,"source_key":<zip key>,"output_prefix":<dest prefix>,"files_uploaded":<count>,"nested_zips_processed":<count>,"max_depth_reached":<bool>}
- On ignore/error: structured reason and diagnostics (e.g., seen keys)
"""
import os
import io
import sys
import json
import mimetypes
import zipfile
import posixpath
from concurrent.futures import ThreadPoolExecutor, as_completed

from qcloud_cos import CosConfig, CosS3Client

# Environment variables expected:
# COS_BUCKET: target COS bucket name
# INPUT_PREFIX: folder (prefix) to listen for zip uploads, e.g., "uploads/"
# OUTPUT_PREFIX: folder (prefix) to write extracted files, e.g., "extracted/"
# MAX_WORKERS: optional, number of parallel uploads (default 16)

REGION = os.getenv("TENCENTCLOUD_REGION") or os.getenv("REGION") or "ap-guangzhou"
SECRET_ID = os.getenv("TENCENTCLOUD_SECRETID") or os.getenv("SECRETID")
SECRET_KEY = os.getenv("TENCENTCLOUD_SECRETKEY") or os.getenv("SECRETKEY")
SESSION_TOKEN = os.getenv("TENCENTCLOUD_SESSIONTOKEN") or os.getenv("SESSIONTOKEN")

COS_BUCKET = os.getenv("COS_BUCKET", "")
INPUT_PREFIX = os.getenv("INPUT_PREFIX", "uploads/")
OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "extracted/")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "16"))
MAX_RECURSION_DEPTH = int(os.getenv("MAX_RECURSION_DEPTH", "10"))

# Ensure prefixes end with '/'
if INPUT_PREFIX and not INPUT_PREFIX.endswith('/'):
    INPUT_PREFIX += '/'
if OUTPUT_PREFIX and not OUTPUT_PREFIX.endswith('/'):
    OUTPUT_PREFIX += '/'

# Setup COS client; in SCF, credentials are provided via env vars or role
_config_kwargs = {
    'Region': REGION,
}
if SECRET_ID and SECRET_KEY:
    _config_kwargs.update({'SecretId': SECRET_ID, 'SecretKey': SECRET_KEY})
if SESSION_TOKEN:
    _config_kwargs.update({'Token': SESSION_TOKEN})

cos_client = CosS3Client(CosConfig(**_config_kwargs))


def _is_safe_member(member_name: str) -> bool:
    # Prevent Zip Slip: reject absolute paths and parent directory traversal
    if member_name.startswith('/'):
        return False
    parts = member_name.split('/')
    for p in parts:
        if p == '..':
            return False
    return True


def _sanitize_member(member_name: str) -> str:
    # Normalize to POSIX path and strip leading './'
    p = posixpath.normpath(member_name)
    if p.startswith('./'):
        p = p[2:]
    return p


def _content_type_for(name: str) -> str:
    ctype, _ = mimetypes.guess_type(name)
    return ctype or 'application/octet-stream'


def _download_to_tmp(bucket: str, key: str, local_path: str):
    resp = cos_client.get_object(Bucket=bucket, Key=key)
    body = resp['Body']
    with open(local_path, 'wb') as f:
        # Stream in chunks to file
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def _upload_object(bucket: str, key: str, data: bytes, content_type: str):
    cos_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def _zipinfo_is_dir(member: zipfile.ZipInfo) -> bool:
    try:
        if member.is_dir():
            return True
    except Exception:
        pass
    # Fallback: check UNIX mode bits in external_attr
    try:
        return ((member.external_attr >> 16) & 0o40000) != 0
    except Exception:
        return False


def _extract_and_upload(zip_path: str, bucket: str, dest_prefix: str, max_depth: int = 10):
    """Extract all non-directory entries from a local zip file and upload them to COS.
    Recursively processes any zip files found within the archive.

    Args:
        zip_path: Absolute path to the downloaded zip in /tmp.
        bucket: Full COS bucket name (e.g., "mybucket-123456789").
        dest_prefix: Prefix in COS under which extracted files will be placed.
        max_depth: Maximum recursion depth to prevent infinite loops (default: 10).

    Behavior:
        - Skips unsafe paths to prevent Zip Slip.
        - Uses robust directory detection to avoid uploading folder placeholders.
        - Uploads files in parallel (MAX_WORKERS).
        - Sets content type via mimetypes.
        - Recursively processes nested zip files up to max_depth levels.
        - Does NOT upload zip files themselves - only extracts and uploads their contents.

    Returns:
        dict: Summary with counts of processed files and nested zips.
    """
    if max_depth <= 0:
        return {'files_uploaded': 0, 'nested_zips_processed': 0, 'max_depth_reached': True}
    
    tasks = []
    nested_zips = []
    files_uploaded = 0
    
    with zipfile.ZipFile(zip_path, 'r') as zf:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for member in zf.infolist():
                name = member.filename
                if not _is_safe_member(name):
                    # Skip unsafe paths
                    continue
                # Skip directory entries (some zippers omit trailing slash)
                if _zipinfo_is_dir(member):
                    continue
                clean_name = _sanitize_member(name)
                if clean_name.endswith('/'):
                    # Directory entry: no upload needed
                    continue
                
                # Read entry data into memory
                with zf.open(member, 'r') as src:
                    data = src.read()
                
                dest_key = posixpath.join(dest_prefix, clean_name)
                content_type = _content_type_for(clean_name)
                
                # Check if this is a nested zip file
                if clean_name.lower().endswith('.zip'):
                    # Save nested zip for recursive processing, but don't upload the zip file itself
                    nested_zip_path = f"/tmp/nested_{len(nested_zips)}_{posixpath.basename(clean_name)}"
                    nested_zips.append({
                        'path': nested_zip_path,
                        'data': data,
                        'dest_prefix': posixpath.join(dest_prefix, clean_name[:-4])  # Remove .zip extension
                    })
                    # Skip uploading the zip file - we only want the extracted contents
                    continue
                
                # Upload non-zip files only
                tasks.append(executor.submit(_upload_object, bucket, dest_key, data, content_type))
                files_uploaded += 1

            # Wait for uploads to complete
            for fut in as_completed(tasks):
                # Propagate any exceptions
                fut.result()

    # Process nested zip files recursively
    nested_zips_processed = 0
    for nested_zip in nested_zips:
        try:
            # Write nested zip data to temp file
            with open(nested_zip['path'], 'wb') as f:
                f.write(nested_zip['data'])
            
            # Recursively extract nested zip
            nested_result = _extract_and_upload(
                nested_zip['path'], 
                bucket, 
                nested_zip['dest_prefix'], 
                max_depth - 1
            )
            nested_zips_processed += 1 + nested_result.get('nested_zips_processed', 0)
            files_uploaded += nested_result.get('files_uploaded', 0)
            
        except Exception as e:
            # Log error but continue processing other nested zips
            print(f"Error processing nested zip {nested_zip['path']}: {str(e)}")
        finally:
            # Cleanup temp file
            try:
                os.remove(nested_zip['path'])
            except Exception:
                pass
    
    return {
        'files_uploaded': files_uploaded,
        'nested_zips_processed': nested_zips_processed,
        'max_depth_reached': max_depth <= 1 and len(nested_zips) > 0
    }


def main_handler(event, context):
    """SCF entrypoint triggered by COS PutObject events.

    Event expectations:
        event['Records'][i]['cos']['cosObject']['key'] holds the object key.
        event['Records'][i]['cos']['cosBucket']['name'] may contain the bucket short name.

    Processing steps:
        - Decode and normalize keys; skip folder markers.
        - Pick first .zip under INPUT_PREFIX.
        - Infer full bucket name if event includes short form.
        - Download zip to /tmp, extract and upload entries to OUTPUT_PREFIX/<zip base>/.
        - Cleanup temp files and return structured result.
    """
    # Event is COS PutObject trigger
    # Expected structure contains Records[0].cos.cosObject.key and cosBucket.name
    try:
        records = event.get('Records') or []
        if not records:
            return {'status': 'ignored', 'reason': 'no records'}

        from urllib.parse import unquote_plus

        cos_bucket = None
        zip_key = None
        seen_keys = []
        for rec in records:
            cos_info = rec.get('cos', {})
            cos_bucket = cos_bucket or cos_info.get('cosBucket', {}).get('name') or COS_BUCKET
            cos_obj = cos_info.get('cosObject', {})
            # Fully URL-decode key
            key = unquote_plus(cos_obj.get('key', '') or '')
            raw_key = key
            # Normalize possible leading "/appid/bucket/" segments and leading slash
            key = key.lstrip('/')
            parts = key.split('/')
            if len(parts) >= 3 and parts[0].isdigit():
                # Check if parts[1] matches the bucket name (with or without appid suffix)
                bucket_name = cos_bucket or ''
                # Try exact match first, then try bucket short name (before '-')
                if parts[1] == bucket_name or (bucket_name and parts[1] == bucket_name.split('-')[0]):
                    key = '/'.join(parts[2:])

            seen_keys.append({'raw': raw_key, 'normalized': key})

            # Skip folder marker events
            if not key or key.endswith('/'):
                continue
            # Skip files in output prefix to prevent infinite loops
            if OUTPUT_PREFIX and key.startswith(OUTPUT_PREFIX):
                continue
            # Find first zip in expected prefix
            if key.lower().endswith('.zip') and (not INPUT_PREFIX or key.startswith(INPUT_PREFIX)):
                zip_key = key
                break

        if not cos_bucket:
            return {'status': 'error', 'message': 'COS_BUCKET not set and not found in event'}
        if not zip_key:
            return {'status': 'ignored', 'reason': 'no zip in records', 'keys': seen_keys, 'bucket': cos_bucket, 'input_prefix': INPUT_PREFIX}

        # Determine output base path: OUTPUT_PREFIX/<zip_basename_without_ext>/
        base = posixpath.basename(zip_key)
        zip_name = base[:-4] if base.lower().endswith('.zip') else base
        dest_prefix = posixpath.join(OUTPUT_PREFIX, zip_name)

        # Recover full bucket name <bucketname>-<appid> if event provided short name
        bucket_to_use = cos_bucket
        if bucket_to_use and '-' not in bucket_to_use:
            # Try to infer from seen keys like "/<appid>/<bucketname>/..."
            for k in seen_keys:
                raw = k.get('raw') or ''
                if raw.startswith('/'):
                    parts = raw.strip('/').split('/')
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1] == bucket_to_use:
                        bucket_to_use = f"{parts[1]}-{parts[0]}"
                        break
        # Fallback to env COS_BUCKET if provided in full form
        if COS_BUCKET and '-' in COS_BUCKET:
            bucket_to_use = COS_BUCKET

        # Ensure tmp paths
        tmp_zip = f"/tmp/{zip_name}.zip"

        _download_to_tmp(bucket_to_use, zip_key, tmp_zip)
        extraction_result = _extract_and_upload(tmp_zip, bucket_to_use, dest_prefix, MAX_RECURSION_DEPTH)

        # Optional: cleanup tmp file
        try:
            os.remove(tmp_zip)
        except Exception:
            pass

        return {  # Return structured result
            'status': 'ok',
            'bucket': cos_bucket,
            'source_key': zip_key,
            'output_prefix': dest_prefix,
            'files_uploaded': extraction_result.get('files_uploaded', 0),
            'nested_zips_processed': extraction_result.get('nested_zips_processed', 0),
            'max_depth_reached': extraction_result.get('max_depth_reached', False),
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


if __name__ == '__main__':
    # Local testing stub: simulate an event with environment variables
    sample_key = os.getenv('TEST_KEY', f"{INPUT_PREFIX}sample.zip")
    event = {
        "Records": [
            {
                "cos": {
                    "cosBucket": {"name": COS_BUCKET or "your-bucket"},
                    "cosObject": {"key": sample_key}
                }
            }
        ]
    }
    result = main_handler(event, None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
