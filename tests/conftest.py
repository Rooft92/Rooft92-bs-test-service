import os
import tempfile
from pathlib import Path

# Настройки читаются при импорте app — задаём окружение до него
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["B24_APP_TOKEN"] = "test-app-token"
