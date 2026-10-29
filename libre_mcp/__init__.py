__version__ = "0.12.1"

# Sign-in and LibreLinkUp access through MyAuth come from the MyCommons
# building block `access` (libre_mcp/access/ is a copy, see MyCommons/sync.sh).
from .access import myauth as _myauth

_myauth.configure(app="mcp-libre", platform="LibreLinkUp")
