from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
class Settings(BaseSettings):
    bot_token: str
    mongodb_uri: str
    database_name: str='storebat'
    admin_ids: str=''
    channel_url: str=''
    support_username: str=''
    public_base_url: str=''
    api_rate_limit_per_minute: int=60
    webhook_secret: str=''
    model_config=SettingsConfigDict(env_file='.env',extra='ignore')
    @property
    def admins(self)->List[int]: return [int(x.strip()) for x in self.admin_ids.split(',') if x.strip().isdigit()]
try:
    settings=Settings()
except Exception as e:
    raise RuntimeError(
        'Configuration error: BOT_TOKEN and MONGODB_URI are required. '
        'Set them in .env (see .env.example) or as environment variables.'
    ) from e
