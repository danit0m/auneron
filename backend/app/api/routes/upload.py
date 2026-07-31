import os
import shutil

from fastapi import APIRouter, UploadFile, File, Depends

from sqlalchemy.orm import Session

from app.database.database import get_db

from app.services.import_service import import_clients



router = APIRouter()



UPLOAD_DIR = "uploads"


os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)



@router.post("/upload/")
async def upload_file(

    file: UploadFile = File(...),

    db: Session = Depends(get_db)

):


    filepath = os.path.join(

        UPLOAD_DIR,

        file.filename

    )


    with open(
        filepath,
        "wb"
    ) as buffer:

        shutil.copyfileobj(
            file.file,
            buffer
        )


    result = import_clients(

        filepath,

        db

    )


    return {

        "arquivo": file.filename,

        "resultado": result,

        "status": "sucesso"

    }