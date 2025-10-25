# Changelog

## v1.4.0 - Current Release

### ✨ New Features
- **Recursive Zip Processing**: Automatically detects and extracts nested zip files up to configurable depth
- **Clean Output**: Extracts contents only - no zip files saved to output folder
- **Processing Statistics**: Returns detailed counts of files uploaded and nested zips processed

### 🛡️ Security & Stability
- **Loop Prevention**: Ignores output prefix files to prevent infinite self-triggering
- **Key Normalization**: Fixed handling of COS event key formats for various bucket naming patterns
- **OpenSSL Compatibility**: Uses urllib3 1.26.20 for SCF Python 3.9 compatibility
- **Depth Limiting**: Configurable `MAX_RECURSION_DEPTH` prevents zip bomb attacks

### 🔧 Configuration
- Added `MAX_RECURSION_DEPTH` environment variable (default: 10)
- Enhanced error reporting with processing statistics
- Improved documentation with comprehensive examples

### 📦 Deployment
- **Package**: `scf-cos-unzipper.zip` (2.3MB)
- **Runtime**: Python 3.9 (required)
- **Handler**: `scf-unzip.main_handler`
- **Dependencies**: All included, no layers required

## Previous Versions

### v1.3.0
- Added recursive nested zip processing
- Preserved original zip files in output

### v1.2.0  
- Added loop prevention for output prefix
- Fixed key normalization issues

### v1.1.0
- Added key normalization for COS event formats
- Fixed OpenSSL compatibility issues

### v1.0.0
- Initial release with basic zip extraction
- Zip Slip protection
- Parallel uploads