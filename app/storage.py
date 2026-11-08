"""Almacenamiento de los archivos que comparten los grupos.

En producción los archivos van a Backblaze B2 por su API compatible con S3 (el disco de Render
se borra en cada deploy). Si B2_BUCKET no está definida (desarrollo local), se guardan en la
carpeta UPLOADS_DIR (por defecto ./uploads).
"""

import os
from functools import lru_cache
from pathlib import Path


class MissingFileError(Exception):
    """El archivo no está en el almacenamiento (por ejemplo, se perdió en un deploy sin B2)."""


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Las claves las genera la API, pero se valida igual que no salgan de la carpeta
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"Clave de almacenamiento inválida: {key}")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as error:
            raise MissingFileError(key) from error

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class B2Storage:
    def __init__(self, bucket: str, endpoint: str, key_id: str, application_key: str):
        import boto3  # Solo se importa si se usa B2

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=application_key,
        )

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def read(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as error:
            raise MissingFileError(key) from error

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache
def get_storage() -> LocalStorage | B2Storage:
    """Dependencia de FastAPI: el almacenamiento configurado por las variables de entorno."""
    bucket = os.getenv("B2_BUCKET")
    if bucket:
        return B2Storage(
            bucket=bucket,
            endpoint=os.environ["B2_ENDPOINT"],
            key_id=os.environ["B2_KEY_ID"],
            application_key=os.environ["B2_APPLICATION_KEY"],
        )
    return LocalStorage(Path(os.getenv("UPLOADS_DIR", "uploads")))
