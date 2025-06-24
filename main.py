from fastapi import FastAPI, UploadFile, File, Request, HTTPException
import os
import json
import time
from contextlib import asynccontextmanager
from typing import Optional
from services import (
    AudiobookMetadataExtractor,
    AAXProcessor,
    conversion_orchestrator,
)
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from config import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up AAX Converter...")
    # Services are initialized automatically via their singletons
    yield
    # Shutdown
    logger.info("Shutting down AAX Converter...")
    # Thread manager handles graceful shutdown automatically


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


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

        # Get activation bytes
        processor = AAXProcessor()
        result = processor.get_activation_bytes(aax_file_path)

        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not get activation bytes: {result['error']}",
            )

        activation_bytes = result["activation_bytes"]

        # Start conversion using orchestrator
        if conversion_orchestrator.start_m4a_conversion(
            filename, aax_file_path, m4a_file_path, activation_bytes
        ):
            return JSONResponse({"status": "started", "message": "Conversion started"})
        else:
            return JSONResponse(
                {"status": "in_progress", "message": "Conversion already in progress"}
            )

    except Exception as e:
        logger.error(f"Error starting conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/convert/status/{filename}")
def get_conversion_status(filename: str, conversion_type: str = "m4a"):
    """Get conversion progress status"""
    progress_data = conversion_orchestrator.get_conversion_status(
        filename, conversion_type
    )
    return JSONResponse(progress_data)


@app.get("/convert/active")
def get_active_conversions():
    """Get all currently active conversions for monitoring"""
    return JSONResponse(conversion_orchestrator.get_active_conversions())


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

        # Get activation bytes
        processor = AAXProcessor()
        result = processor.get_activation_bytes(aax_file_path)

        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not get activation bytes: {result['error']}",
            )

        activation_bytes = result["activation_bytes"]

        # Start conversion using orchestrator (sequential by default)
        if conversion_orchestrator.start_mp3_conversion(
            filename, aax_file_path, output_dir, activation_bytes, parallel=False
        ):
            return JSONResponse(
                {
                    "status": "started",
                    "message": "MP3 chapters conversion started (sequential)",
                }
            )
        else:
            return JSONResponse(
                {
                    "status": "in_progress",
                    "message": "MP3 conversion already in progress",
                }
            )

    except Exception as e:
        logger.error(f"Error starting MP3 conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/convert/mp3/parallel/{filename}")
def start_mp3_conversion_parallel(filename: str, max_workers: Optional[int] = None):
    """Start AAX to MP3 chapters conversion in background with parallel processing"""
    try:
        # Validate filename
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        aax_file_path = os.path.join("uploads", filename)
        if not os.path.exists(aax_file_path):
            raise HTTPException(status_code=404, detail="AAX file not found")

        # Create output directory for zip file
        output_dir = "uploads"

        # Get activation bytes
        processor = AAXProcessor()
        result = processor.get_activation_bytes(aax_file_path)

        if "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Could not get activation bytes: {result['error']}",
            )

        activation_bytes = result["activation_bytes"]

        # Start parallel conversion using orchestrator
        if conversion_orchestrator.start_mp3_conversion(
            filename, aax_file_path, output_dir, activation_bytes, parallel=True
        ):
            # Determine worker count for user feedback
            if max_workers is None:
                max_workers = min(os.cpu_count() or 4, 4)

            return JSONResponse(
                {
                    "status": "started",
                    "message": f"MP3 chapters conversion started (parallel with {max_workers} workers)",
                    "parallel": True,
                    "max_workers": max_workers,
                }
            )
        else:
            return JSONResponse(
                {
                    "status": "in_progress",
                    "message": "MP3 conversion already in progress",
                }
            )

    except Exception as e:
        logger.error(f"Error starting parallel MP3 conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/mp3/{filename}")
def download_mp3_zip(filename: str):
    """Download the converted MP3 chapters zip file"""
    try:
        if not filename.endswith(".aax"):
            raise HTTPException(status_code=400, detail="Invalid file format")

        # Get the conversion record to find the zip file path
        progress_data = conversion_orchestrator.get_conversion_status(
            filename, "mp3_chapters"
        )

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
