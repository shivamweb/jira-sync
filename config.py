import os
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv


@dataclass
class JiraInstance:
    base_url: str
    email: str
    token: str
    project_key: str


@dataclass
class Config:
    source: JiraInstance
    target: JiraInstance
    sync_attachments: bool
    db_path: str


def load_config() -> Config:
    load_dotenv()
    with open("config.yml", "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return Config(
        source=JiraInstance(
            base_url=os.environ["SOURCE_BASE_URL"],
            email=os.environ["SOURCE_EMAIL"],
            token=os.environ["SOURCE_TOKEN"],
            project_key=raw["source_project"],
        ),
        target=JiraInstance(
            base_url=os.environ["TARGET_BASE_URL"],
            email=os.environ["TARGET_EMAIL"],
            token=os.environ["TARGET_TOKEN"],
            project_key=raw["target_project"],
        ),
        sync_attachments=bool(raw.get("sync_attachments", True)),
        db_path=raw.get("db_path", "data/mapping.sqlite"),
    )
