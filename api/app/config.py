"""アプリケーション設定。環境変数から読み込む。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = (
        "postgresql+psycopg://postgres:postgres@db:5432/invoice"
    )

    # 参照データ（Excel）の置き場。docker-compose でマウントする
    fixtures_dir: str = "/fixtures"

    # 発行済みPDFの保管先。洗い替えでも消さない
    storage_dir: str = "/storage"

    # CORS。カンマ区切りで複数指定できる
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
