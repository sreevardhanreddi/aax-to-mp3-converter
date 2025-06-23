from fastapi import FastAPI, UploadFile, File, Request
import os
from services import AudiobookMetadataExtractor, AAXProcessor
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from config import logger

app = FastAPI()
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
