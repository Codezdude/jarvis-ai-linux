from pathlib import Path
from modules.instagram_agent import (
    resolve_public_image_url,
    publish_to_instagram_graph,
)

CAPTION = """You've heard "it's too much stomach acid." Usually it isn't — and the fix depends on which drug class you reach for.

Antacids (like calcium carbonate) neutralize acid already in your stomach — fast, short-lived. H2 blockers (like famotidine) tell acid-making cells to ease off. Proton pump inhibitors (PPIs) shut down the acid pump itself, so they work slower but last longer. "Acid reflux" is usually acid moving the wrong way, not your stomach making too much. None of these fix the cause, and they aren't interchangeable — a doctor or pharmacist can help you pick. Red flag: trouble swallowing, weight loss you didn't intend, or black stools — get those checked, not self-treated.

Save this

#health #wellness #acidreflux #guthealth #HealthLexicon"""

img = Path("data/ig_media/ig_acid_actually.png")
url = resolve_public_image_url(img)
print("public url:", url)
media_id = publish_to_instagram_graph(url, CAPTION)
print("published media id:", media_id)