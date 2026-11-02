"""Резервная копия журнала из командной строки: python manage.py backup.

Команда собирает ту же копию, что и кнопка «Скачать копию» в разделе
«Резервная копия» (``journal.backup.build_backup``), и записывает её в файл.
Её вызывает ``update.sh`` перед обновлением; код возврата 0 — успех, 1 — ошибка.

Вывод команды — на английском: её запускает установщик и советует README,
а журнал публикуется для международной аудитории.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from journal import backup as backup_tools
from journal import crypto


class Command(BaseCommand):
    help = "Writes a journal backup to a JSON file (optionally encrypted)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            metavar="PATH",
            help=(
                "file to write; default: backup-YYYYMMDD-HHMMSS.json "
                "in the current folder"
            ),
        )
        parser.add_argument(
            "--password",
            metavar="PASSPHRASE",
            help="encrypt the backup with this passphrase (PBKDF2 + HMAC)",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="print nothing on success",
        )

    def handle(self, *args, **options):
        passphrase = options.get("password") or ""
        target = options.get("output")
        if target:
            path = Path(target).expanduser()
        else:
            stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
            path = Path.cwd() / f"backup-{stamp}.json"

        try:
            payload = backup_tools.dumps(backup_tools.build_backup()).encode("utf-8")
            if passphrase:
                payload = crypto.encrypt_bytes(payload, passphrase)
            # Каталог назначения создаём, если его ещё нет: так удобно писать
            # в backups/ из update.sh и по расписанию.
            if path.parent != Path(".") and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError as error:
            raise CommandError(
                f"Could not write the backup file {path}: {error}"
            ) from error
        except ValueError as error:
            raise CommandError(f"Could not build the backup: {error}") from error
        except Exception as error:  # noqa: BLE001 - код возврата 1 вместо трассировки
            raise CommandError(f"Could not build the backup: {error}") from error

        if not options.get("quiet"):
            size = path.stat().st_size
            encrypted = ", encrypted" if passphrase else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backup written: {path.resolve()} ({size} bytes{encrypted})"
                )
            )
