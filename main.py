import configparser
import os
from runningRouteApp.factory import create_app

config = configparser.ConfigParser()
config.read('pymongo_ini')

if __name__ == "__main__":
    app = create_app()
    app.config['DEBUG'] = True
    app.run()
    
    
app = create_app()
