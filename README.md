# SCF COS Unzipper

A robust Tencent Serverless Cloud Function (SCF) that automatically unzips uploaded archives in Cloud Object Storage (COS) with recursive processing capabilities.

**Important**: This function works with archives up to **10GB** (SCF hard limit).

## Features

🔄 **Recursive Processing**: Automatically detects and extracts nested zip files up to configurable depth  
🛡️ **Security**: Prevents Zip Slip attacks and path traversal vulnerabilities  
⚡ **Performance**: Parallel uploads with configurable worker threads  
🎯 **Smart Filtering**: Ignores output files to prevent infinite loops  
📁 **Clean Output**: Extracts contents only - no zip files in output folder  
🔧 **Configurable**: Extensive environment variable configuration  

## How it works

1. **Listens** to COS PutObject events for `.zip` files in `INPUT_PREFIX`
2. **Downloads** zip to `/tmp` inside SCF runtime
3. **Extracts** files safely (prevents Zip Slip, skips directories)
4. **Processes recursively** - detects nested zips and extracts them to subdirectories
5. **Uploads** extracted files in parallel to `OUTPUT_PREFIX/<zip-name>/...`
6. **Cleans up** temporary files and returns processing statistics

## Security & Safety

- **Zip Slip Protection**: Rejects absolute paths and `..` traversal attempts
- **Robust Directory Detection**: Uses `ZipInfo.is_dir()` and Unix mode bits to avoid 0-byte folder objects
- **Key Normalization**: Handles COS event key formats including `"/appid/bucket/..."` patterns
- **Loop Prevention**: Ignores files in output prefix to prevent self-triggering
- **Depth Limiting**: Configurable recursion depth prevents zip bomb attacks

## Recursive Processing Example

```
Input: main.zip containing:
  - document.pdf
  - images.zip containing:
    - photo1.jpg
    - photo2.jpg
  - data.zip containing:
    - spreadsheet.xlsx
    - archive.zip containing:
      - backup.txt

Output in COS:
  unzipper-output/main/
  ├── document.pdf
  ├── images/
  │   ├── photo1.jpg
  │   └── photo2.jpg
  └── data/
      ├── spreadsheet.xlsx
      └── archive/
          └── backup.txt
```

**Note**: Only extracted files are uploaded - zip files themselves are not saved to output.

## Environment Variables

### Required
- `COS_BUCKET`: Target COS bucket (prefer full form `"bucket-appid"`)
- `INPUT_PREFIX`: Prefix to watch for zip uploads (e.g., `"unzip-in/"`)
- `OUTPUT_PREFIX`: Prefix to write extracted files (e.g., `"unzipper-output/"`)

### Optional
- `REGION` or `TENCENTCLOUD_REGION`: COS region (default: `"ap-guangzhou"`)
- `MAX_WORKERS`: Parallel upload threads (default: `16`)
- `MAX_RECURSION_DEPTH`: Maximum nesting levels (default: `10`)

### Credentials (usually auto-provided by SCF role)
- `TENCENTCLOUD_SECRETID`, `TENCENTCLOUD_SECRETKEY`, `TENCENTCLOUD_SESSIONTOKEN`
- Alternative: `SECRETID`, `SECRETKEY`, `SESSIONTOKEN`

## Deployment

### 1. Create SCF Function
- Runtime: **Python 3.9**
- Handler: `scf-unzip.main_handler`
- Memory: 512MB minimum
- Timeout: 300s (5 minutes)

### 2. Set Environment Variables
```bash
COS_BUCKET=your-bucket-123456789
INPUT_PREFIX=unzip-in/
OUTPUT_PREFIX=unzipper-output/
MAX_RECURSION_DEPTH=10
MAX_WORKERS=16
```

### 3. Configure COS Trigger
- Event type: `cos:ObjectCreated:Put`
- Bucket: Same as `COS_BUCKET`
- Prefix filter: Same as `INPUT_PREFIX` (e.g., `unzip-in/`)
- Suffix filter: `.zip` (recommended)

### 4. Attach CAM Role
Create a role with the following permissions:

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

Replace placeholders:
- `<REGION>`: e.g., `ap-guangzhou`
- `<APPID>`: Your Tencent Cloud account APPID
- `<BUCKET>`: Bucket short name (without `-appid` suffix)
- `<INPUT_PREFIX>`: e.g., `unzip-in/`
- `<OUTPUT_PREFIX>`: e.g., `unzipper-output/`

## Response Format

### Success
```json
{
  "status": "ok",
  "bucket": "your-bucket",
  "source_key": "unzip-in/archive.zip",
  "output_prefix": "unzipper-output/archive",
  "files_uploaded": 15,
  "nested_zips_processed": 3,
  "max_depth_reached": false
}
```

### Ignored/Error
```json
{
  "status": "ignored",
  "reason": "no zip in records",
  "keys": [{"raw": "...", "normalized": "..."}],
  "bucket": "your-bucket",
  "input_prefix": "unzip-in/"
}
```

## Local Testing

Set environment variables and run:

```bash
export COS_BUCKET="your-bucket-123456789"
export INPUT_PREFIX="unzip-in/"
export OUTPUT_PREFIX="unzipper-output/"
export TEST_KEY="unzip-in/test.zip"
python scf-unzip.py
```

## Dependencies

- `cos-python-sdk-v5>=1.9.24`
- `urllib3<2.0.0` (for SCF OpenSSL compatibility)
- `requests>=2.25.0,<3.0.0`

## Implementation Notes

- **Concurrency**: Uses `ThreadPoolExecutor` for parallel uploads
- **Content Types**: Determined via `mimetypes.guess_type`
- **Memory Usage**: Loads individual files into memory (suitable for typical file sizes)
- **Temp Space**: Uses SCF's `/tmp` directory for processing
- **Error Handling**: Continues processing other files if individual nested zips fail

## Limitations

- **File Size**: Individual files limited by available memory and SCF constraints
- **Archive Size**: Total archive size limited to 10GB (SCF limit)
- **Processing Time**: Function timeout applies to entire processing (configure accordingly)
- **Concurrency**: Nested zips processed sequentially to manage memory usage

## Version History

- **v1.0**: Basic zip extraction
- **v1.1**: Added key normalization for COS event formats
- **v1.2**: Added loop prevention for output prefix
- **v1.3**: Added recursive nested zip processing
- **v1.4**: Removed zip file upload - extract contents only