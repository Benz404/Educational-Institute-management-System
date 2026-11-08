import shutil
import sqlite3
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Back up the database and uploaded question papers into backups/ (timestamped)."

    def handle(self, *args, **opts):
        src = settings.DATABASES["default"]["NAME"]
        dest_dir = settings.BASE_DIR / "backups"
        dest_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = dest_dir / f"db-{stamp}.sqlite3"
        with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
            source.backup(target)
        media = settings.MEDIA_ROOT
        if media.exists() and any(p.is_file() and p.name != ".gitkeep" for p in media.rglob("*")):
            shutil.make_archive(str(dest_dir / f"papers-{stamp}"), "zip", media)
        self.stdout.write(self.style.SUCCESS(f"Backup written to {dest_dir}"))
