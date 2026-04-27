"""JSON-backed storage helpers for collected projects."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


DB_FILENAME = "projects_db.json"

DEFAULT_DB: Dict[str, Any] = {
    "metadata": {
        "total_projects": 0,
        "first_project_date": None,
        "last_project_date": None,
        "last_scan_date": None,
        "total_scans": 0,
        "last_scan_added_count": 0,
        "last_page_scanned": 1,
        "last_coin_index": 0,
        "next_scan_id": 1,
    },
    "projects": [],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Storage:
    db_path: Path

    @classmethod
    def from_base_dir(cls, base_dir: Path) -> "Storage":
        data_dir = Path(base_dir)
        return cls(data_dir / DB_FILENAME)

    def ensure_db(self) -> None:
        if not self.db_path.exists():
            self.write_db(deepcopy(DEFAULT_DB))
            return

        try:
            self.read_db()
        except (json.JSONDecodeError, OSError):
            backup_path = self.db_path.with_suffix(".corrupt.json")
            shutil.copy2(self.db_path, backup_path)
            self.write_db(deepcopy(DEFAULT_DB))

    def read_db(self) -> Dict[str, Any]:
        self.ensure_parent()
        with self.db_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return self._merge_defaults(data)

    def write_db(self, data: Dict[str, Any]) -> None:
        self.ensure_parent()
        with self.db_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

    def ensure_parent(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _merge_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        merged = deepcopy(DEFAULT_DB)
        merged["metadata"].update(data.get("metadata", {}))
        merged["projects"] = data.get("projects", [])
        merged["metadata"]["total_projects"] = len(merged["projects"])
        return merged

    def is_duplicate(self, coin_id: str, twitter_handle: str) -> bool:
        db = self.read_db()
        handle = twitter_handle.lower()
        for project in db["projects"]:
            if project.get("coin_id") == coin_id:
                return True
            existing_handle = (project.get("twitter_handle") or "").lower()
            if existing_handle and existing_handle == handle:
                return True
        return False

    def add_projects(
        self,
        projects: List[Dict[str, Any]],
        last_page_scanned: int,
        last_coin_index: int,
    ) -> Dict[str, Any]:
        db = self.read_db()
        metadata = db["metadata"]
        timestamp = utc_now_iso()
        scan_id = metadata.get("next_scan_id", 1)

        added_count = 0
        for project in projects:
            if self.is_duplicate(project["coin_id"], project["twitter_handle"]):
                continue

            record = deepcopy(project)
            record["date_added"] = timestamp
            record["scan_id"] = scan_id
            db["projects"].append(record)
            added_count += 1

        metadata["total_projects"] = len(db["projects"])
        metadata["last_scan_date"] = timestamp
        metadata["last_scan_added_count"] = added_count
        metadata["total_scans"] = metadata.get("total_scans", 0) + 1
        metadata["last_page_scanned"] = last_page_scanned
        metadata["last_coin_index"] = last_coin_index
        metadata["next_scan_id"] = scan_id + 1

        if added_count and not metadata.get("first_project_date"):
            metadata["first_project_date"] = timestamp
        if added_count:
            metadata["last_project_date"] = timestamp

        self.write_db(db)
        return {"scan_id": scan_id, "added_count": added_count, "total_projects": metadata["total_projects"]}

    def recent_projects(self, limit: int = 10) -> List[Dict[str, Any]]:
        db = self.read_db()
        return list(reversed(db["projects"]))[:limit]

    def search_projects(self, query: str) -> List[Dict[str, Any]]:
        needle = query.strip().lower()
        if not needle:
            return []
        db = self.read_db()
        return [
            project
            for project in db["projects"]
            if needle in project.get("name", "").lower() or needle in project.get("symbol", "").lower()
        ]

    def stats(self) -> Dict[str, Any]:
        db = self.read_db()
        metadata = db["metadata"]
        total_scans = metadata.get("total_scans", 0)
        total_projects = len(db["projects"])
        average_projects = (total_projects / total_scans) if total_scans else 0
        return {
            "total_projects": total_projects,
            "first_project_date": metadata.get("first_project_date"),
            "last_project_date": metadata.get("last_project_date"),
            "last_scan_date": metadata.get("last_scan_date"),
            "total_scans": total_scans,
            "average_projects_per_scan": round(average_projects, 2),
        }

    def export_text(self) -> str:
        db = self.read_db()
        projects = db["projects"]
        if not projects:
            return ""

        lines = ["name,symbol,market_cap,twitter_url,telegram_url,category,date_added,scan_id"]
        for project in projects:
            lines.append(
                ",".join(
                    [
                        _csv_escape(project.get("name", "")),
                        _csv_escape(project.get("symbol", "")),
                        str(project.get("market_cap", "")),
                        _csv_escape(project.get("twitter_url", "")),
                        _csv_escape(project.get("telegram_url", "")),
                        _csv_escape(project.get("category", "")),
                        _csv_escape(project.get("date_added", "")),
                        str(project.get("scan_id", "")),
                    ]
                )
            )
        return "\n".join(lines)

    def clear(self) -> None:
        self.write_db(deepcopy(DEFAULT_DB))

    def resume_state(self) -> Dict[str, int]:
        db = self.read_db()
        metadata = db["metadata"]
        return {
            "last_page_scanned": int(metadata.get("last_page_scanned", 1) or 1),
            "last_coin_index": int(metadata.get("last_coin_index", 0) or 0),
        }


def _csv_escape(value: str) -> str:
    text = str(value).replace('"', '""')
    return f'"{text}"'
