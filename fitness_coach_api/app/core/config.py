from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name:str
    environment:str
    mongodb_uri:str
    mongodb_database:str
    
    
    model_config = SettingsConfigDict(
        env_file= ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra= "ignore"
        
    )
    
    
#Initalize Settings
settings= Settings()