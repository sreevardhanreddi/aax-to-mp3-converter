from fastapi import FastAPI, UploadFile, File, Request, HTTPException
import os
import json
import threading
import time
from contextlib import asynccontextmanager
from services import AudiobookMetadataExtractor, AAXProcessor
from models import ConversionTracker
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from config import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up AAX Converter...")
    # Initialize SQLite-based conversion tracker
    global conversion_tracker
    conversion_tracker = ConversionTracker()
    # Clean up old conversion records on startup
    conversion_tracker.cleanup_old_records(days=2)
    yield
    # Shutdown
    logger.info("Shutting down AAX Converter...")


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# Global conversion tracker instance
conversion_tracker = None


@app.get("/")
def read_root(request: Request):
    # Home page now shows uploads list
    uploads = os.listdir("uploads")
    uploads = [f for f in uploads if f.endswith(".aax")]
    # process the file and get the metadata
    metadata = []
    for upload in uploads:
        file_path = os.path.join("uploads", upload)

        extractor = AudiobookMetadataExtractor(file_path)
        extractor.extract_full_metadata()
        metadata.append(extractor.get_complete_metadata())
    return templates.TemplateResponse(
        "uploads.html",
        {"request": request, "uploads": uploads, "metadata": metadata},
    )


@app.get("/uploads")
def read_uploads(request: Request):
    # Uploads page now shows upload form
    return templates.TemplateResponse(
        "index.html", {"request": request, "metadata": None}
    )


@app.get("/detail/{filename}")
def read_detail(request: Request, filename: str):
    try:
        file_path = os.path.join("uploads", filename)
        if not os.path.exists(file_path) or not filename.endswith(".aax"):
            return templates.TemplateResponse(
                "detail.html",
                {"request": request, "filename": filename, "metadata": None},
            )

        # Extract complete metadata for detailed view
        processor = AAXProcessor()
        result = processor.get_activation_bytes(file_path)

        extractor = AudiobookMetadataExtractor(file_path)
        extractor.extract_full_metadata()
        metadata = extractor.get_complete_metadata()

        return templates.TemplateResponse(
            "detail.html",
            {
                "request": request,
                "filename": filename,
                "metadata": metadata,
                "activation_bytes": result.get("activation_bytes", "N/A"),
                "checksum": result.get("checksum", "N/A"),
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "detail.html",
            {"request": request, "filename": filename, "metadata": None},
        )


@app.get("/health")
def read_root():
    return {"message": "OK"}


@app.delete("/delete/{filename}")
def delete_file(filename: str):
    try:
        file_path = os.path.join("uploads", filename)
        if os.path.exists(file_path) and filename.endswith(".aax"):
            os.remove(file_path)
            return {"message": "File deleted successfully"}
        else:
            return {"error": "File not found"}, 404
    except Exception as e:
        return {"error": str(e)}, 500


def convert_file_background(
    filename: str, aax_file_path: str, m4a_file_path: str, activation_bytes: str
):
    """Background function to handle file conversion with progress tracking"""
    try:
        if os.path.exists(m4a_file_path):
            os.remove(m4a_file_path)
        # Start conversion tracking in database
        conversion_tracker.start_conversion(filename, "m4a")

        processor = AAXProcessor()

        # Define progress callback function
        def progress_callback(progress_percent):
            conversion_tracker.update_progress(
                filename, round(progress_percent, 1), "converting", "m4a"
            )
            logger.info(f"Conversion progress for {filename}: {progress_percent:.1f}%")

        # Update status to converting
        conversion_tracker.update_progress(filename, 0, "converting", "m4a")

        success = processor.convert_to_m4a(
            aax_file_path, m4a_file_path, activation_bytes, progress_callback
        )

        # Mark conversion as completed or failed
        if success:
            conversion_tracker.complete_conversion(
                filename, success=True, result_path=m4a_file_path, conversion_type="m4a"
            )
        else:
            conversion_tracker.complete_conversion(
                filename,
                success=False,
                error_message="Conversion failed",
                conversion_type="m4a",
            )

    except Exception as e:
        conversion_tracker.complete_conversion(
            filename, success=False, error_message=str(e), conversion_type="m4a"
        )


def convert_mp3_chapters_background(
    filename: str, aax_file_path: str, output_dir: str, activation_bytes: str
):
    """Background function to handle MP3 chapter conversion with progress tracking"""
    try:
        # Start conversion tracking in database
        conversion_tracker.start_conversion(filename, "mp3_chapters")

        processor = AAXProcessor()

        # Define progress callback function
        def progress_callback(progress_percent):
            conversion_tracker.update_progress(
                filename, round(progress_percent, 1), "converting", "mp3_chapters"
            )
            logger.info(
                f"MP3 chapters conversion progress for {filename}: {progress_percent:.1f}%"
            )

        # Update status to converting
        conversion_tracker.update_progress(filename, 0, "converting", "mp3_chapters")

        result = processor.convert_to_mp3_chapters(
            aax_file_path, output_dir, activation_bytes, progress_callback
        )

        # Mark conversion as completed or failed
        if result["success"]:
            conversion_tracker.complete_conversion(
                filename,
                success=True,
                result_path=result["zip_path"],
                conversion_type="mp3_chapters",
            )
        else:
            conversion_tracker.complete_conversion(
                filename,
                success=False,
                error_message=result.get("error", "MP3 conversion failed"),
                conversion_type="mp3_chapters",
            )

    except Exception as e:
        conversion_tracker.complete_conversion(
            filename,
            success=False,
            error_message=str(e),
            conversion_type="mp3_chapters",
        )


@app.post("/convert/{filename}")
def start_conversion(filename: str):
    """Start AAX to M4A conversion in background"""
    try:
        # Validate filename
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        aax_file_path = os.path.join("uploads", filename)
        if not os.path.exists(aax_file_path):
            raise HTTPException(status_code=404, detail="AAX file not found")

        # Create output filename and path
        base_name = os.path.splitext(filename)[0]
        m4a_filename = f"{base_name}.m4a"
        m4a_file_path = os.path.join("uploads", m4a_filename)

        # Check if already converted and up to date
        if os.path.exists(m4a_file_path) and os.path.getmtime(
            aax_file_path
        ) <= os.path.getmtime(m4a_file_path):
            return JSONResponse(
                {
                    "status": "already_converted",
                    "message": "File already converted",
                    "download_url": f"/download/{filename}",
                }
            )

        # Check if conversion is already in progress
        if conversion_tracker.is_conversion_active(filename, "m4a"):
            return JSONResponse(
                {"status": "in_progress", "message": "Conversion already in progress"}
            )

        # Get activation bytes
        processor = AAXProcessor()
        result = processor.get_activation_bytes(aax_file_path)

        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not get activation bytes: {result['error']}",
            )

        activation_bytes = result["activation_bytes"]

        # Start conversion in background thread
        thread = threading.Thread(
            target=convert_file_background,
            args=(filename, aax_file_path, m4a_file_path, activation_bytes),
        )
        thread.daemon = True
        thread.start()

        return JSONResponse({"status": "started", "message": "Conversion started"})

    except Exception as e:
        logger.error(f"Error starting conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/convert/status/{filename}")
def get_conversion_status(filename: str, conversion_type: str = "m4a"):
    """Get conversion progress status"""
    progress_data = conversion_tracker.get_progress(filename, conversion_type)

    # If completed, add download URL
    if progress_data["status"] == "completed":
        if conversion_type == "m4a":
            progress_data["download_url"] = f"/download/{filename}"
        elif conversion_type == "mp3_chapters":
            progress_data["download_url"] = f"/download/mp3/{filename}"

    return JSONResponse(progress_data)


@app.get("/convert/active")
def get_active_conversions():
    """Get all currently active conversions for monitoring"""
    active_conversions = conversion_tracker.get_all_active_conversions()
    return JSONResponse(
        {"active_conversions": active_conversions, "count": len(active_conversions)}
    )


@app.get("/download/{filename}")
def download_m4a(filename: str):
    """Download the converted M4A file"""
    try:
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        base_name = os.path.splitext(filename)[0]
        m4a_filename = f"{base_name}.m4a"
        m4a_file_path = os.path.join("uploads", m4a_filename)

        if not os.path.exists(m4a_file_path):
            raise HTTPException(status_code=404, detail="Converted file not found")

        return FileResponse(
            path=m4a_file_path, filename=m4a_filename, media_type="audio/mp4"
        )

    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload/file/aax")
def upload_file_aax(request: Request, file: UploadFile = File(...)):
    file_path = os.path.join("uploads", file.filename)
    with open(file_path, "wb") as f:
        f.write(file.file.read())

    # Extract metadata using activation bytes
    processor = AAXProcessor()
    result = processor.get_activation_bytes(file_path)

    extractor = AudiobookMetadataExtractor(file_path)
    extractor.get_complete_metadata_using_activation_bytes(result["activation_bytes"])

    metadata = extractor.get_complete_metadata()
    # return templates.TemplateResponse(
    #     # "index.html", {"request": request, "metadata": metadata}
    # )
    # return metadata
    return RedirectResponse(url="/", status_code=303)


@app.post("/convert/mp3/{filename}")
def start_mp3_conversion(filename: str):
    """Start AAX to MP3 chapters conversion in background"""
    try:
        # Validate filename
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        aax_file_path = os.path.join("uploads", filename)
        if not os.path.exists(aax_file_path):
            raise HTTPException(status_code=404, detail="AAX file not found")

        # Create output directory for zip file
        output_dir = "uploads"

        # Check if conversion is already in progress
        if conversion_tracker.is_conversion_active(filename, "mp3_chapters"):
            return JSONResponse(
                {
                    "status": "in_progress",
                    "message": "MP3 conversion already in progress",
                }
            )

        # Get activation bytes
        processor = AAXProcessor()
        result = processor.get_activation_bytes(aax_file_path)

        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not get activation bytes: {result['error']}",
            )

        activation_bytes = result["activation_bytes"]

        # Start conversion in background thread
        thread = threading.Thread(
            target=convert_mp3_chapters_background,
            args=(filename, aax_file_path, output_dir, activation_bytes),
        )
        thread.daemon = True
        thread.start()

        return JSONResponse(
            {"status": "started", "message": "MP3 chapters conversion started"}
        )

    except Exception as e:
        logger.error(f"Error starting MP3 conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/mp3/{filename}")
def download_mp3_zip(filename: str):
    """Download the converted MP3 chapters zip file"""
    try:
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        # Get the conversion record to find the zip file path
        progress_data = conversion_tracker.get_progress(filename, "mp3_chapters")

        if progress_data["status"] != "completed" or not progress_data.get(
            "result_path"
        ):
            raise HTTPException(
                status_code=404, detail="MP3 conversion not completed or file not found"
            )

        zip_file_path = progress_data["result_path"]

        if not os.path.exists(zip_file_path):
            raise HTTPException(status_code=404, detail="Converted zip file not found")

        zip_filename = os.path.basename(zip_file_path)

        return FileResponse(
            path=zip_file_path, filename=zip_filename, media_type="application/zip"
        )

    except Exception as e:
        logger.error(f"Error downloading MP3 zip file: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
