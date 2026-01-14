import sys
import site

# Add the virtual environment's site-packages to the path
site.addsitedir('/var/www/zebby/faderbank/venv/lib/python3.9/site-packages')

# Add your project directory to the path
sys.path.insert(0, '/var/www/zebby/faderbank')

from app import app as application
