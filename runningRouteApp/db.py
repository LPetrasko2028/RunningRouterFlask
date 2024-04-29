
from json import dumps
from flask import current_app, g
from werkzeug.local import LocalProxy
from flask_pymongo import PyMongo
import sys
from math import ceil

from datetime import datetime, timezone

def get_db():
    """
    Configuration method to return db instance
    """
    db = getattr(g, "_database", None)

    if db is None:
        mongo = PyMongo(current_app).cx['main']
        db = g._database = mongo.db
    
    return db


# Use LocalProxy to read the global db instance with just `db`
db = LocalProxy(lambda: get_db())


#Retrieves the adjacency list from mongodb using the field:
#- 'email'; retrieved via login and being sent in process with the request
#TODO: 
def getAdjList(email):
    list = db.adjacencyLists.find_one({'email': email})
    return list

#add a new adjacency list
def addAdjList(email, list, center, radius, coordArray, startid, direction):
    currList = getAdjList(email)
    if currList:
        print("Duplicate Email")
        return False
    newList = {"email": email,"list": dumps(list), 
               "createdAt": datetime.now(timezone.utc), 
               "center": center, 
               "radius": radius, 
               "coordArray": dumps(coordArray), 
               "startid": startid,
               "direction": direction}
    success = False
    try:
        db.adjacencyLists.insert_one(newList)
        db.extraInfo.insert_one(newList)
        print("No caught exception")
        success = True
    except Exception as e:
        print("Error on the insert: ", e)
    return success

#update an adjacency list if the user email already has an existing list
def updateAdjListFull(email, list, center, radius, coordArray, startid,  direction):
    
    try:
        listSize = sys.getsizeof(list)
        numExtraInfo = int(ceil(listSize / 100000))
        list = dumps(list)
        """
        newList = []
        if (numExtraInfo > 1):
            newList = [ list[i:i+int(listSize/numExtraInfo)] for i in range(0, numExtraInfo, int(listSize/numExtraInfo)) ]
            print(len(newList))
            list = dumps(newList[0])"""
            
        response = db.adjacencyLists.update_one(
            {"email": email},
            {"$set": {"list": list, 
                      "createdAt": datetime.now(timezone.utc), 
                      "center": center, 
                      "radius": radius, 
                      "coordArray": dumps(coordArray), 
                      "startid": startid,
                      "direction": direction}},
            upsert = True
        )
        """
        if (numExtraInfo > 1):
            for index, element in enumerate(newList[1:]):
                responseExtra = db.extraInfo.update_one(
                    {"email": email},
                    {"$set": 
                        {
                        "list": dumps(element), 
                        "createdAt": datetime.now(timezone.utc), 
                        "coordArray": dumps(coordArray),
                        "documentNum": index+1
                        }
                    },
                    upsert = True
                )"""
        return response
    except Exception as e:
        print("Error: ", e)
        return None
    
def updateAdjListTTL(email):
    
    try:
        response = db.adjacencyLists.update_one(
            {"email": email},
            {"$set": {"createdAt": datetime.now(timezone.utc)}}
        )
        return response
    except Exception as e:
        print("Error: ", e)
        return None

#delete an adjacency list (I dont think I will see this because I will overwrite or rely on TTL)
def deleteAdjList(email):
    try:
        response = db.adjacencyLists.delete_one({"email": email})
    except Exception as e:
        print("Error Deleting List from Mongo: ", e)
        return None

    return response
