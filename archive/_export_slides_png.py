"""One-off: export every slide of PADB_Simple_and_Interactive.pptx to PNG via
PowerPoint COM automation, for visual review."""
import os
import win32com.client

PPTX = r"C:\apps\padb\tools\PADB_Simple_and_Interactive.pptx"
OUT_DIR = r"C:\apps\padb\tools\_slide_previews"
os.makedirs(OUT_DIR, exist_ok=True)

app = win32com.client.Dispatch("PowerPoint.Application")
app.Visible = True
pres = app.Presentations.Open(PPTX, WithWindow=False)
pres.SaveAs(OUT_DIR, 18)  # ppSaveAsPNG -> exports one PNG per slide into OUT_DIR
pres.Close()
app.Quit()
print("Exported to", OUT_DIR)
