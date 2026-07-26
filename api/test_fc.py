import os
from firecrawl import Firecrawl
app = Firecrawl(api_key=os.environ.get("FIRECRAWL_API_KEY"))
print(app.map("https://afloat.ie/sail/", search="race"))
