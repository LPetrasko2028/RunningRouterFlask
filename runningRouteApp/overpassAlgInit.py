import requests, json
import geopy.distance
from multiprocessing import Lock, Process
import multiprocessing
from threading import Semaphore
from math import sqrt
from .db import updateAdjListTTL
import random
from time import sleep, time
from itertools import pairwise
import networkx as nx
import matplotlib.pyplot as plt



#This function fixes the bounding box around the start location to help limit the number of nodes as well as add a neat utility to the user
#Parameters include
# direction: (North, South, East, West, North-East, North-West, South-East, South-West)
# lat: latitude positioning in Decimal Degrees
# lon: longitude positioning in Decimal Degrees
# distMile: The distance in miles
def fixBoundingBox(direction, lat, lon, distMile):

    bboxFixedCoords = {"minLat": 0, "minLon": 0, "maxLat": 0, "maxLon": 0}
    bboxFixedCoords["minLon"] = geopy.distance.distance(miles=distMile).destination(geopy.Point(lat,lon), bearing=270).longitude
    bboxFixedCoords["maxLon"] = geopy.distance.distance(miles=distMile).destination(geopy.Point(lat,lon), bearing=90).longitude
    bboxFixedCoords["minLat"] = geopy.distance.distance(miles=distMile).destination(geopy.Point(lat,lon), bearing=180).latitude
    bboxFixedCoords["maxLat"] = geopy.distance.distance(miles=distMile).destination(geopy.Point(lat,lon), bearing=0).latitude
    if (direction == 'North'):
        bboxFixedCoords["minLon"] = lon
    elif (direction == 'East'):
        bboxFixedCoords["minLat"] = lat
    elif (direction == 'South'):
        bboxFixedCoords["maxLon"] = lon
    elif (direction == 'West'):
        bboxFixedCoords["maxLat"] = lat
    elif (direction == 'North-East'):
        topRight = geopy.distance.distance(miles=sqrt(2*distMile*distMile)).destination(geopy.Point(lat,lon), bearing=45)
        bboxFixedCoords["minLon"] = lon
        bboxFixedCoords["maxLon"] = topRight.longitude
        bboxFixedCoords["minLat"] = lat
        bboxFixedCoords["maxLat"] = topRight.latitude
    elif (direction == 'South-East'):
        bottomRight = geopy.distance.distance(miles=sqrt(2*distMile*distMile)).destination(geopy.Point(lat,lon), bearing=135)
        bboxFixedCoords["minLon"] = bottomRight.longitude
        bboxFixedCoords["maxLon"] = lon
        bboxFixedCoords["minLat"] = lat
        bboxFixedCoords["maxLat"] = bottomRight.latitude
    elif (direction == 'South-West'):
        bottomLeft = geopy.distance.distance(miles=sqrt(2*distMile*distMile)).destination(geopy.Point(lat,lon), bearing=225)
        bboxFixedCoords["minLon"] = bottomLeft.longitude
        bboxFixedCoords["maxLon"] = lon
        bboxFixedCoords["minLat"] = bottomLeft.latitude
        bboxFixedCoords["maxLat"] = lat
    elif (direction == 'North-West'):
        topLeft = geopy.distance.distance(miles=sqrt(2*distMile*distMile)).destination(geopy.Point(lat,lon), bearing=315)        
        bboxFixedCoords["minLon"] = lon
        bboxFixedCoords["maxLon"] = topLeft.longitude
        bboxFixedCoords["minLat"] = topLeft.latitude
        bboxFixedCoords["maxLat"] = lat
    else:
        #throw an error
        print("")
    
    #fix the coordinates to have 7 decimals, arbitrarily chosen
    bboxFixedCoords["minLon"] = int(bboxFixedCoords["minLon"] * 10000)/10000
    bboxFixedCoords["maxLon"] = int(bboxFixedCoords["maxLon"] * 10000)/10000
    bboxFixedCoords["minLat"] = int(bboxFixedCoords["minLat"] * 10000)/10000
    bboxFixedCoords["maxLat"] = int(bboxFixedCoords["maxLat"] * 10000)/10000
    return bboxFixedCoords

# when the user queries to generate a route, the start point might not correspond with a node on a highway tag,
# so this finds the nearest coordinate that will be within the area
#lat: latitude
#lon: longitude
#mileage: distance goal, but in this case it is used as the vicinity to check around
def findCheckStart(lat, lon, mileage, list = None):
    meters = 100
    min = mileage
    while meters/2 < min and min == mileage:
        query = '''
            [out:json];
            (
                way(around: {0}, {1}, {2})["highway"="residential"];
                way(around: {0}, {1}, {2})["highway"="secondary"];
                way(around: {0}, {1}, {2})["highway"="tertiary"];
                way(around: {0}, {1}, {2})["highway"="unclassified"];
                way(around: {0}, {1}, {2})["highway"="primary"];
            );
            (._;>;);
            out body;
        '''.format(meters, float(lat), float(lon))

        overPass_url = "https://overpass-api.de/api/interpreter"
        query_params = {"data": query}
        failed = True
        count = 0
        while failed and count < 5:
            try:
                response = requests.post(overPass_url, data=query_params)
                result = response.json()
                failed = False
            except requests.exceptions.ConnectionError as re: 
                print("Error: ", re)
            sleep(1)

        #finds the distance of the nodes that were found if any, the closest one is set as the start and returns
        minNode = None
        if list:
            for node in result['elements']:
                if node["type"] == "node":
                    if str(node["id"]) in list:
                        tempD = geopy.distance.distance((lat, lon), (node['lat'], node['lon']))
                        if tempD < min: 
                            min = tempD
                            minNode = node
        else:
            for node in result['elements']:
                if node["type"] == "node":
                    tempD = geopy.distance.distance((lat, lon), (node['lat'], node['lon']))
                    if tempD < min: 
                        min = tempD
                        minNode = node
        meters *= 2

    if minNode is None: return False
    return minNode["lat"], minNode["lon"] , minNode["id"]
        

#This is the first method called to begin the route gen, specifically finding all the nodes and ways located in the area selected
#mileage: the mileage goal for the route
#lat: latitude
#lon: longitude
#direction: direction is of the form North, East, South, West, North-East, North-West, South-East, South-West
#-----------This is used to limit the bounding box in the query to help prevent excess data
def overpassQuery(mileage, lat, lon, direction):
    #find the start location
    radius = 1609.344 * float(mileage)/2.0
    try:
        print("Find correct start")
        lat, lon, startid = findCheckStart(lat, lon, radius)
    except Exception as e:
        print("Error: ", e)
        print("Start point not within 2 mile area, recommended to find a new start")
        return None
    #fix the bounding box
    print("Fix bounding box")
    coordsForBBox = fixBoundingBox(None, float(lat), float(lon), float(mileage)/2)
    bboxString = '[bbox: {}, {}, {}, {}]'.format(coordsForBBox["minLat"],coordsForBBox["minLon"], coordsForBBox["maxLat"], coordsForBBox["maxLon"])
    query = '''
        [out:json]{3};
        (
            way(around: {0}, {1}, {2})["highway"="residential"];
            way(around: {0}, {1}, {2})["highway"="secondary"];
            way(around: {0}, {1}, {2})["highway"="tertiary"];
            way(around: {0}, {1}, {2})["highway"="unclassified"];
            way(around: {0}, {1}, {2})["highway"="primary"];
        );
        (._;>;);
        out body;
    '''.format(radius, float(lat), float(lon), bboxString)
    overPass_url = "https://overpass-api.de/api/interpreter"
    query_params = {"data": query}
    #This is the actual query using the data above
    failed = True
    count = 0
    while failed and count < 5:
        try:
            response = requests.post(overPass_url, data=query_params)
            result = response.json()
            failed = False
        except requests.exceptions.ConnectionError as re: 
            print("Error: ", re)
        sleep(1)
    #print(json.dumps(result, indent=2))
    return result, lat, lon, startid


#Create an adjacency list from the given object/dict received from the overpass query
#orderedDict: The dictionary recieved with the nodes and the ways from overpass
#num: the number of workers/processes being created
def optimizeForAdjListMulti(orderedDict, num):
    adjList = multiprocessing.Manager().dict()
    coordArray = multiprocessing.Manager().list()
    sp = multiprocessing.Manager().Semaphore(1)
    dictToList = orderedDict["elements"]
    wayList = []
    nodeList = []
    for element in dictToList:
        if element["type"] == 'node':
            nodeList.append(element)
        else:
            wayList.append(element)
    
    interval_size = int(len(wayList)/num)
    
    processes = []
    for x in range(0, num):
        newP = Process(target=multiProcessTwo, args=(wayList[x * interval_size: (x+1)* interval_size], nodeList, coordArray, adjList, sp))
        newP.start()
        processes.append(newP)

    for process in processes:
        process.join()


    return dict(adjList), list(coordArray)

#the target process in the multiprocessing, this will create the adjacency list
#waylist: The list of ways from the overpass query
#nodelist: the list of nodes from the overpass query
#coordArray: The coordinate array will house all the nodes only once with the lats and lons
#------------The index of each element is used to navigate the adjacency List
#adjList: The adjacency list being created, the first element is the coordinate location in the coord array
#---------and the second is the list of adjacencies in the form of coordArray index, weight
def multiProcessTwo(wayList, nodeList, coordArray, adjList, sp):
     #go through every way
    localAdjList = dict()
    for element in wayList:
        roadType = element["tags"]["highway"]
        previousNode = -1
        #go through every node inside the way
        for node in element["nodes"]:
            #search for the actual node corresponding to the id of the 'node' variable above
            for el in nodeList:
                #if the type is not node, it is a way, just skip out. It is an arbitrary spot usually roughly half, where it switches
                #but it is not set in stone at any one time
                if el["type"] != "node": break
                if el["id"] == node:
                    newNode = True
                    #if the node is new, add a new element to the dict and add an adjacency from previous to it and it to previous
                    sp.acquire()
                    try:
                        if node not in adjList:  
                            #print(node)          
                            coordArray.append(el)
                            #the first element will be its location in coordArray
                            adjList[node] = [len(coordArray)-1]
                        
                        else: newNode = False
                        #Get the distance to become the weight for the edge of the adjacency (option to switch to km?)
                        if previousNode != -1:
                            lat1 = coordArray[adjList[node][0]]['lat']
                            lon1 = coordArray[adjList[node][0]]['lon']
                            lat2 = coordArray[previousNode]['lat']
                            lon2 = coordArray[previousNode]['lon']
                            #in miles
                            distanceToNode = int(geopy.distance.distance((lat1, lon1), (lat2, lon2)).miles * 100000) / 100000
                            #if the node is in the dict/adjList, we won't add it, but we will have to add the previous node as an adjacent and v.v.
                            adjList[node].append([coordArray[previousNode]["id"], distanceToNode])
                            idOfLast = coordArray[previousNode]["id"]
                            adjList[idOfLast].append([el["id"],distanceToNode])
                    except:
                        print("Error adding adjacencies:")
                        print(el)
                        print(coordArray[len(coordArray)-1])
                    finally:
                        sp.release()
                    
                    #if this was a new node
                    if newNode:
                        previousNode = len(coordArray)-1
                    #otherwise we want it to be the node at the old location
                    else:
                        previousNode = adjList[node][0]
                    break

#The threadless implementation of converting data to an adjacency list
#Threadless was chosen due to python's GIL and the overhead due to multiprocessing
#multiprocessing has the potential to be used when finding multiple routes because they
#will not have to share resources between processes
#orderedDict: This is the dictionary provided by overpass
#-------------I only care about the ["elements"], in which the format of those is
#-------------a list of nodes with coordinates followed by a list of ways which house a list of nodeIds, 
#-------------searchable only from the above nodes
def createAdjListThreadless(orderedDict):
    adjList = dict()
    coordArray = list()
    dictToList = orderedDict["elements"]

    #go through every way
    for element in reversed(dictToList):
        if element["type"] == "node": break
        roadType = element["tags"]["highway"]
        previousNode = -1
        #go through every node inside the way
        for index, node in enumerate(element["nodes"]):
            #if index % 3 == 0:
                #search for the actual node corresponding to the id of the 'node' variable above
                for el in dictToList:
                    #if the type is not node, it is a way, just skip out. It is an arbitrary spot usually roughly half, where it switches
                    #but it is not set in stone at any one time
                    if el["type"] != "node": break
                    if el["id"] == node:
                        newNode = True
                        #if the node is new, add a new element to the dict and add an adjacency from previous to it and it to previous
                        if node not in adjList:  
                            #print(node)          
                            coordArray.append(el)
                            #the first element will be its location in coordArray
                            adjList[str(node)] = [len(coordArray)-1]
                        
                        else: newNode = False
                        #Get the distance to become the weight for the edge of the adjacency (option to switch to km?)
                        if previousNode != -1:
                            lat1 = coordArray[adjList[str(node)][0]]['lat']
                            lon1 = coordArray[adjList[str(node)][0]]['lon']
                            lat2 = coordArray[previousNode]['lat']
                            lon2 = coordArray[previousNode]['lon']
                            #in miles
                            distanceToNode = int(geopy.distance.distance((lat1, lon1), (lat2, lon2)).miles * 100000) / 100000
                            #if the node is in the dict/adjList, we won't add it, but we will have to add the previous node as an adjacent and v.v.
                            adjList[str(node)].append([str(coordArray[previousNode]["id"]), distanceToNode])
                            idOfLast = coordArray[previousNode]["id"]
                            adjList[str(idOfLast)].append([str(el["id"]),distanceToNode])
                    
                        #if this was a new node
                        if newNode:
                            previousNode = len(coordArray)-1
                        #otherwise we want it to be the node at the old location
                        else:
                            previousNode = adjList[str(node)][0]
                        break

    return adjList, coordArray

#given the cumbersome data to process, I am going to limit ways to their endpoints and insert the midpoints when constructing the path
#I will still have to loop through like before, including checking if a node is in the list already in order to add it, but the issue to overcome
#is tracking the way id and how to associate it between each node. Perhaps I can have an extra element in each adjacency telling me which way it is connected by
# I will have to be careful not to do it backwards however, so when inserting I have to check that
#AdjacencyList Format at an id: [[nodeIdConnected, distance, wayIdConnectedBy],[...],[...]]
def endpointList(orderedDict):
    adjList = dict()
    coordArray = dict()
    wayList = dict()
    dictToList = orderedDict["elements"]
    print("", file=open('logging.txt', 'w'))
    #go through every way
    for element in reversed(dictToList):
        if element["type"] == "node": break
        wayList[str(element["id"])] = element["nodes"]
        wayLength = 0
        first = ""
        #cycle through each pair of nodes in the way
        for index, (curNode, nextNode) in enumerate(pairwise(element["nodes"])):
            #go through each node in the list to find its associated 
            #add the first element to the adjacency list and coordArray
            if index == 0 and str(curNode) not in coordArray:
                if str(curNode) not in adjList:
                    adjList[str(curNode)] = []
                for el in dictToList:
                    if el["type"] == "way":
                        break
                    if str(el["id"]) == str(curNode):
                        coordArray[str(curNode)] = el
                        break
            #all nodes will be added to the coordArray
            if str(nextNode) not in coordArray:
                if str(nextNode) not in adjList:
                    adjList[str(nextNode)] = []
                for el in dictToList:
                    if el["type"] == "way":
                        break
                    if str(el["id"]) == str(nextNode):
                        coordArray[str(nextNode)] = el
                        break
            
            #find the distance between each node, the total distance is stored for each endpoint adjacency
            lat1 = coordArray[str(curNode)]['lat']
            lon1 = coordArray[str(curNode)]['lon']
            lat2 = coordArray[str(nextNode)]['lat']
            lon2 = coordArray[str(nextNode)]['lon']
            wayLength += int(geopy.distance.distance((lat1, lon1), (lat2, lon2)).miles * 100000) / 100000
            
            adjList[str(curNode)].append([str(nextNode), wayLength, element["id"]])
            adjList[str(nextNode)].append([str(curNode), wayLength, element["id"]])
                
    
    #print("\n\n\nPruning excess\n\n\n", file=open('logging.txt', 'a'))
    #print("Total List start: ", adjList, file=open('logging.txt', 'a'))
    #prune nodes of degree 2 unless they are endpoints of their way
    keysToDelete = []
    for nodeSet in adjList:
        #print(nodeSet, file=open('logging.txt', 'a'))
        #print(adjList[nodeSet], file=open('logging.txt', 'a'))
        if len(adjList[str(nodeSet)]) == 2:
            #print("len == 2", file=open('logging.txt', 'a'))
            #we need to check that the node is not at the endpoint of a way
            firstAdjacency = adjList[str(nodeSet)][0]
            secondAdjacency = adjList[str(nodeSet)][1]
            #print("neighbor 1: ", firstAdjacency, file=open('logging.txt', 'a'))
            #print("neighbor 2: ", secondAdjacency, file=open('logging.txt', 'a'))
            #wayToCheck = wayList[str(firstAdjacency[2])]
            #print("Checking ways of neighbors:\n", str(firstAdjacency[2]), "==", str(secondAdjacency[2]), file=open('logging.txt', 'a'))
            if (str(firstAdjacency[2]) == str(secondAdjacency[2])):
                #now that we know they are not an endpoint, I have to remove this adjacency and connect the other two adjacent nodes
                #print("We can safely delete this node from the list, add it to the keyList", file=open('logging.txt', 'a'))
                keysToDelete.append(str(nodeSet))
    #print("\n\n\nFinally prepping to delete\n\n\n", file=open('logging.txt', 'a'))
    for key in keysToDelete:
        #print("Current key: ",key,": ", adjList[str(key)], file=open('logging.txt', 'a'))
        #print("List to check, we are deleting\n",adjList, file=open('logging.txt', 'a'))
        firstNeigh = adjList[key][0]
        secondNeigh = adjList[key][1]

        #print("Neighbor 1: ", firstNeigh, "\nNeighbor 2: ", secondNeigh, file=open('logging.txt', 'a'))
        newDist = firstNeigh[1] + secondNeigh[1]
        firstNeigh[1] = newDist
        secondNeigh[1] = newDist
        #print("New distance if we delete\nNeighbor 1: ", firstNeigh, "\nNeighbor 2: ", secondNeigh, file=open('logging.txt', 'a'))
        #print("\tFirst: ", adjList[str(firstNeigh[0])], file=open('logging.txt', 'a'))
        #print("\tSecond: ", adjList[str(secondNeigh[0])], file=open('logging.txt', 'a'))
        adjList[str(firstNeigh[0])].append(secondNeigh)
        adjList[str(secondNeigh[0])].append(firstNeigh)
        #print("New adjacencies to connect first and second: ", file=open('logging.txt', 'a'))
        #print("\tFirst: ", adjList[str(firstNeigh[0])], file=open('logging.txt', 'a'))
        #print("\tSecond: ", adjList[str(secondNeigh[0])], file=open('logging.txt', 'a'))
        #print("Key to delete: ",key,":=>", adjList[key], file=open('logging.txt', 'a'))
        adjList.pop(key)
        #try:
         #   print("After: ", adjList[key], file=open('logging.txt', 'a'))
        #except Exception as e:
         #   print("key was popped, cannot add", file=open('logging.txt', 'a'))
        #print("Now to remove its connections from the two adjacent nodes it had", file=open('logging.txt', 'a'))
        #I also need to remove key from its adjacent adjacencies
        for index, adjNode in enumerate(adjList[str(firstNeigh[0])]):
            #print(str(adjNode[0]),"==", str(key), file=open('logging.txt', 'a'))
            if str(adjNode[0]) == str(key):
                #print("Equal", file=open('logging.txt', 'a'))
                #print(adjList[str(firstNeigh[0])][index], "=", key, file=open('logging.txt', 'a'))
                #print("Before: ", adjList[str(firstNeigh[0])], file=open('logging.txt', 'a'))
                adjList[str(firstNeigh[0])].pop(index)
                #print("After: ", adjList[str(firstNeigh[0])], file=open('logging.txt', 'a'))
                break
        for index, adjNode in enumerate(adjList[str(secondNeigh[0])]):
            #print(str(adjNode[0]),"==", str(key), file=open('logging.txt', 'a'))
            if str(adjNode[0]) == str(key):
                #print("Equal", file=open('logging.txt', 'a'))
                #print(adjList[str(secondNeigh[0])][index], "=", key, file=open('logging.txt', 'a'))
                #print("Before: ", adjList[str(secondNeigh[0])], file=open('logging.txt', 'a'))
                adjList[str(secondNeigh[0])].pop(index)
                #print("After: ", adjList[str(secondNeigh[0])], file=open('logging.txt', 'a'))
                break
       
    #print("Done", file=open('logging.txt', 'a'))
            


    return adjList, coordArray, wayList

#this function takes in a list pulled from mongodb and the data provided by the user, it will check a few conditions
#1) if the distance between the starting lat and lon and that provided by the user is greater than the mileage, a new list is needed
#2) if the radius of data saved is less than that of the requested data/2, a new one is needed
#3) otherwise it will be  allow the existing list setting lat lon and adjlist permanently in this use of the algorithm
def validateExistingList(data, existingList):
    #2.3 if it exists, we have our start node from the data element and we will have saved the start node with the list in mongodb to simplify this step
        #----We can find the distance between these two points, if the distance is greater than the mileage, we definitely need one
        lat = existingList["center"][0]
        lon = existingList["center"][1]
        adjList = dict()
        newNeeded = False
        distanceToNode = int(geopy.distance.distance((lat, lon), (data['lat'], data['lon'])).miles * 100000) / 100000
        if distanceToNode > float(data["mileage"])/2: 
            print("new needed: distance > radius")
            lat=data['lat']
            lon=data['lon']
            newNeeded = True
        
        #2.4 another concern is the same start node but different distance. if the distance is larger, we need a new list, otherwise it is okay and we can reuse it
        else:
            if float(data["mileage"])/2 > float(existingList["radius"]): 
                print("new needed: radius > existing radius")
                lat=data['lat']
                lon=data['lon']
                newNeeded = True
            #2.5 update the TTL/date for the list
            else:
                adjList = json.loads(existingList["list"])
                print("Mongo Query: update TTL")
                updateAdjListTTL(data["email"])    
        return newNeeded, lat, lon, adjList

#TODO:new process implemented here, break the length into at least 4 sections, once it is 4 miles, go mile by mile as each section
#before we use the algorithm, we are going to break the route into n segments for an n length route
def findCheckPoints(mileage, direction, lat, lon, id, list):
    checkpoints = []
    checkpoints.append([lat,lon,id])
    lastLat = lat
    lastLon = lon
    bearingDegree = 300
    if direction == 'North-East': bearingDegree=(bearingDegree+45)%360
    elif direction == 'East': bearingDegree=(bearingDegree+90)%360
    elif direction == 'South-East': bearingDegree=(bearingDegree+135)%360
    elif direction == 'South': bearingDegree=(bearingDegree+180)%360
    elif direction == 'South-West': bearingDegree=(bearingDegree+225)%360
    elif direction == 'West': bearingDegree=(bearingDegree+270)%360
    elif direction == 'North-West': bearingDegree=(bearingDegree+315)%360
    else: bearingDegree = 300
    
    if mileage < 4:
        bearingInterval = 45
        for x in range(0, 4):
            genRand = random.randint(-1,1)
            if x != mileage - 1:
                coords = geopy.distance.distance(miles=mileage/4).destination(geopy.Point(lastLat,lastLon), bearing=(bearingDegree+genRand)%360)
                try:
                    latitude, longitude, newid = findCheckStart(coords.latitude, coords.longitude, mileage/4, list)
                    checkpoints.append([latitude, longitude, newid])
                    bearingDegree+=bearingInterval
                    lastLat = latitude
                    lastLon = longitude
                except Exception as e:
                    print("Error: ", e)
                    print("None returned when finding point, may not exist")
            else:
                checkpoints.append([lat,lon,id])
    else:
        #Assume North of not provided: TODO Change this if it is not found and different direction needed
        #from the beginning lat and lon, find a checkpoint a mile or less away a certain degree above the x axis.
        #use this bearing +- 1 or 2 degrees of bearing to allow for some random for the rest of the segments starting at the one just found
        #at the last segment, just connect it to the starting node
        #Below modulus 360
        #North: +0 degrees bearing, North-East: +45 degrees bearing, East: +90, SE: +135, S: +180, SW: +225, W: +270, NW: +315
        bearingInterval = (180)/(mileage-2)
        for x in range(0, mileage):
            print(x)
            genRand = random.randint(-5,20)
            if x != mileage - 1:
                coords = geopy.distance.distance(miles=0.75).destination(geopy.Point(lastLat,lastLon), bearing=(bearingDegree+genRand)%360)
                try:
                    start = time()
                    latitude, longitude, newid = findCheckStart(coords.latitude, coords.longitude, 500, list)
                    end = time() - start
                    print(end)
                    checkpoints.append([latitude, longitude, newid])
                    bearingDegree+=bearingInterval+genRand
                    lastLat = latitude
                    lastLon = longitude
                except Exception as e:
                    print("Error: ", e)
                    print("None returned when finding point, may not exist")
            else:
                checkpoints.append([lat, lon, id])
    return checkpoints

def generateDataForOutput(adjList, coordArray):
    G = nx.Graph()
    for node in adjList:
        for neighbor in adjList[str(node)]:
            orig = coordArray[str(node)]
            curNeighbor = coordArray[str(neighbor[0])]
            G.add_node(node, pos=(orig['lon'], orig['lat']))
            G.add_node(curNeighbor['id'], pos=(curNeighbor['lon'], curNeighbor['lat']))
            G.add_edge(node, curNeighbor['id'], weight=neighbor[1])
    
    return G