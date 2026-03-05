"""Allow running as: python -m subtitle_tool [--gui]"""

import sys

if "--gui" in sys.argv:
    from subtitle_tool.gui import main
else:
    from subtitle_tool.cli import main

main()
