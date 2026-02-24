 from dataclasses import dataclass
 import os
 
 from dotenv import load_dotenv
 
 
 load_dotenv()
 
 
 @dataclass(frozen=True)
 class Settings:
     bot_token: str
     admin_id: int
-    catalog_url: str
 
 
 def load_settings() -> Settings:
     token = os.getenv("BOT_TOKEN", "")
     admin_id_raw = os.getenv("ADMIN_ID", "")
-    catalog_url = os.getenv(
-        "CATALOG_URL",
-        "https://docs.google.com/spreadsheets/d/1ArypBF_3ifwFoMIxQrvlPNiToVox6WHhIULsLkITsLI/edit?usp=drivesdk",
-    )
 
     if not token:
         raise RuntimeError("BOT_TOKEN is not set. Add it to environment or .env file.")
     if not admin_id_raw:
         raise RuntimeError("ADMIN_ID is not set. Add it to environment or .env file.")
 
-    return Settings(bot_token=token, admin_id=int(admin_id_raw), catalog_url=catalog_url)
+    return Settings(bot_token=token, admin_id=int(admin_id_raw))
