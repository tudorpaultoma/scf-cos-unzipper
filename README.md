# SCF COS Unzipper

A Tencent Serverless Cloud Function (SCF) that automatically unzips newly uploaded archives in Cloud Object Storage (COS) and writes the extracted files back to COS under a structured prefix.

## What it does

- Listens to COS PutObject events.
- Detects the first `.zip` in the event that is under `INPUT_PREFIX`.
- Downloads the zip to `/tmp` inside the SCF runtime.
- Safely extracts files (prevents Zip Slip, skips directories).
- Uploads extracted files in parallel to `OUTPUT_PREFIX/<zip-base-name>/...` with proper `Content-Type` set via `mimetypes`.
- Cleans up temporary files.

## Why this is safe and robust

- Prevents Zip Slip by rejecting absolute paths and any `..` traversal.
- Robustly identifies directory entries using `ZipInfo.is_dir()` and the Unix mode bits in `external_attr` to avoid uploading 0-byte "folder" objects.
- Fully URL-decodes object keys and normalizes leading `"/appid/bucket/..."` forms emitted by COS triggers.

## Environment variables

- `COS_BUCKET`: Target COS bucket. Prefer full form `"<bucket>-<appid>"`. If the event supplies only the short bucket name, the function attempts to infer the full name.
- `INPUT_PREFIX`: Prefix to watch for zip uploads (e.g., `"unzipper-input/"`).
- `OUTPUT_PREFIX`: Prefix to write extracted files (e.g., `"unzipper-output/"`).
- `REGION` or `TENCENTCLOUD_REGION`: COS region (default: `ap-guangzhou`).
- `TENCENTCLOUD_SECRETID`, `TENCENTCLOUD_SECRETKEY`, `TENCENTCLOUD_SESSIONTOKEN`: Credentials injected by SCF role. Alternatively `SECRETID`, `SECRETKEY`, `SESSIONTOKEN`.
- `MAX_WORKERS`: Optional concurrency for parallel uploads (default: `16`).

## Permissions (CAM Policy)

Attach a role with the following minimum permissions (replace placeholders):

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": ["cos:HeadBucket", "cos:ListBucket"],
      "resource": ["qcs::cos:<REGION>:uid/<APPID>:<BUCKET>-<APPID>"]
    },
    {
      "effect": "allow",
      "action": ["cos:GetObject"],
      "resource": ["qcs::cos:<REGION>:uid/<APPID>:<BUCKET>-<APPID>/<INPUT_PREFIX>*"]
    },
    {
      "effect": "allow",
      "action": ["cos:PutObject"],
      "resource": ["qcs::cos:<REGION>:uid/<APPID>:<BUCKET>-<APPID>/<OUTPUT_PREFIX>*"]
    }
  ]
}
```

Placeholders:
- `<REGION>`: e.g., `ap-guangzhou`
- `<APPID>`: Your Tencent Cloud account appid
- `<BUCKET>`: Bucket short name (without the `-appid` suffix)
- `<INPUT_PREFIX>`: Input prefix (must end with `/`), e.g., `unzipper-input/`
- `<OUTPUT_PREFIX>`: Output prefix (must end with `/`), e.g., `unzipper-output/`

Note: You may scope `GetObject` to only `.zip` files if desired: `.../<INPUT_PREFIX>*` is usually sufficient.

## Deployment steps

1. Create an SCF function (Python 3.7+ runtime recommended).
2. Upload `scf-unzip.py` and set the handler to `scf-unzip.main_handler`.
3. Set environment variables as needed:
   - `COS_BUCKET`, `INPUT_PREFIX`, `OUTPUT_PREFIX`, `REGION`, `MAX_WORKERS`.
4. Attach the CAM role with the policy above.
5. Configure a COS Trigger:
   - Event type: PutObject
   - Filter prefix: your `INPUT_PREFIX`
   - Optionally filter suffix: `.zip`
6. Test by uploading a zip to `INPUT_PREFIX`.

## Local testing

You can invoke the script locally with a simulated event. Set `TEST_KEY` env var to the path of a zip under `INPUT_PREFIX` or leave default.

```bash
export INPUT_PREFIX="unzipper-input/"
export OUTPUT_PREFIX="unzipper-output/"
export COS_BUCKET="your-bucket-<appid>"
python scf-unzip.py
```

Output will be a JSON summary.

## Implementation notes

- Concurrency: Uses a `ThreadPoolExecutor` for parallel uploads; tune `MAX_WORKERS` for your typical file sizes and network conditions.
- Content types: Determined via `mimetypes.guess_type`, defaulting to `application/octet-stream`.
- Temp space: SCF provides `/tmp` for temporary files.
- Error handling: Returns a structured error message; consider integrating with logging/observability for production.

## Limitations / Future improvements

- Very large entries are currently read fully into memory before upload; consider streaming the entry file-like object directly to `put_object` for huge files.
- No retry/backoff on upload failures; SCF retries may cover transient issues, but explicit retries can be added.
- No checksum validation between extracted data and uploaded objects; add if required for compliance.