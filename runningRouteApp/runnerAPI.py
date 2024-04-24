import os
from flask import request, jsonify
import requests
import json
from collections import OrderedDict
import runningRouteApp.overpassAlgInit as rt
from runningRouteApp.aStarAlg import searchRunner
from geopy import distance
from geopy.geocoders import Nominatim
from flask import Blueprint
from flask_cors import CORS
import runningRouteApp.db as rdb
import networkx as nx
import geopy as gp
from itertools import pairwise
import traceback
import matplotlib.pyplot as plt
from multiprocessing import Manager, Process, Queue



runnerBP = Blueprint('runner', __name__, template_folder='templates')
CORS(runnerBP)

#tutorial default route
@runnerBP.route('/')
def home():
    return "Hello world!"

#tutorial route to print a name
@runnerBP.route('/hello/<name>', methods=['GET'])
def user(name):
    return f"Hello {name}!"

# Getting coordinates from an address
@runnerBP.route('/getCoordinates', methods=['POST'])
def getCoordinatesFromAddress():
    geolocator = Nominatim(user_agent="RunningApp")
    data = request.form
    address = data['address']
    location = geolocator.geocode(address)
    print((location.latitude, location.longitude))
    return [location.latitude, location.longitude]

#testing getting the start location within a region to check it is valid
@runnerBP.route('/startTesting', methods=['POST'])
def testGetCorrectStart():
    data = request.form
    meters = 20
    min = 1000
    while meters/2 < 1000 and min == 1000:
        query = '''
            [out:json];

            //Get all nodes around 80 meters of the coordinates and asign it to the output set .aroundnodes
            node(around:{},{}, {})->.aroundnodes;

            //get the ways using the input set aroundnodes, but bn is backward, then it filters highways of the specific tag. This uses a regular expression denoted by the ~, then ^ is start, $ is end, and the | is for or
            way(bn.aroundnodes)[highway~"^(residential|primary|secondary|tertiary|unclassified)$"]->.allways;

            //this finds the nodes of all ways and puts them into waynodes
            node(w.allways)->.waynodes;

            //union
            (
            //combines both sets of nodes and the ways into one set to be printed
            node.waynodes.aroundnodes; 
            //way.allways.allways; 
            );

            //prints the previous set put into the default set ._
            out body;
        '''.format(meters, float(data['lat']), float(data['lon']))

        overPass_url = "https://overpass-api.de/api/interpreter"
        query_params = {"data": query}
        response = requests.post(overPass_url, data=query_params)
        result = response.json()

        minNode = None
        for node in result['elements']:
            tempD = distance.distance((data['lat'], data['lon']), (node['lat'], node['lon']))
            print('distance: ' + str(tempD))
            if tempD < min: 
                min = tempD
                minNode = node
        meters *= 2
        print(meters)

    return minNode

#test query to get data from overpass
@runnerBP.route("/test", methods=['POST'])
def getNodesAndWays():
    data = request.form
    radius = 1609.344 * float(data["mileage"])/2.0
    if (data["address"] != "NULL"):
        location = True #temp data
        # geocode to get coords, else just use lat and lon

    query = '''
        [out:json];
        nw(around: {}, {}, {})["highway"];
        (._;>;);
        out body;
    '''.format(radius, float(data["lat"]), float(data["lon"]))
    overPass_url = "https://overpass-api.de/api/interpreter"
    query_params = {"data": query}
    response = requests.post(overPass_url, data=query_params)
    result = response.json()
    print(json.dumps(result, indent=2))
    return result

#test query figuring out how to set up the adjacency list retrieved from mongo
@runnerBP.route("/mongoTesting", methods=['POST'])
def getLists():
    data = request.form
    list = getAdjList(data["email"])
    list["list"] = json.loads(list["list"])
    return list


#test query with networkx
@runnerBP.route("/netXOverpass", methods=['POST'])
def getGraph():
    data = request.form
    print("Overpass Query")
    result, lat, lon = rt.overpassQuery(data["mileage"], data["lat"], data["lon"], data["direction"])

    # Create an empty NetworkX graph
    G = nx.Graph()
    coords = dict()
    # Extract nodes and edges from the response
    for element in reversed(result['elements']):
        if element['type'] == 'way':
            nodes = element['nodes']
            for i in range(len(nodes) - 1):
                if nodes[i] not in coords:
                    coords[result['elements'][i]['id']] = (result["elements"][i])
                distanceToNode = int(gp.distance.distance((result['elements'][i]['lat'], result['elements'][i]['lat']), (result['elements'][i+1]['lat'], result['elements'][i+1]['lat'])).miles * 100000) / 100000
                
                G.add_edge(nodes[i], nodes[i+1], weight=distanceToNode)
        else: break
    return result

@runnerBP.route("/endpointTesting", methods=['POST'])
def testEndpoint():
    data = request.form
    lat = data["lat"]
    lon = data["lon"]
    try:
        result, lat, lon, startid = rt.overpassQuery(data['mileage'], lat, lon, data['direction'])
    except Exception as e:
        print("Error:", e)
        print("Failed to query Overpass")
        return e
    listSize = 0
    for node in result["elements"]:
        if node["type"] == "node": listSize+=1
    try:
        orderedResult = OrderedDict(result)
    except Exception as e:
        print(e.__traceback__)
        print("Error ", e)
        print("Element was not a dict, cannot continue the algorithm")
        return result
    
    adjList, coordArray = rt.endpointList(orderedResult)
    return adjList

#current main query, begins by getting data from overpass, it turns it into an orderedDict, and then 
#turns the data into an adjacency list to be used for the algorithm
@runnerBP.route("/overpassGather", methods=['POST'])
def bundlePythonResults():
    #1 get data sent by this request, mileage/lat/lon/direction/ "user info"
    data = request.form
    newNeeded = False
    exisitingList = {}
    startid=0
    #print(data)
    lat = data["lat"]
    lon = data["lon"]

    #2 check if we need to create a new adjacency list
    #2.1 if the request is not provided with an email, the user is not logged in, skip this because we need to save data with an email
    if data["email"]:
        #2.2 get the saved adjacency_list. If it doesn't exist, we can skip and go to 3 to create it
        existingList = rdb.getAdjList(data["email"])
        if existingList:
            newNeeded, lat, lon, tempList = rt.validateExistingList(data, existingList)
            if tempList: 
                adjList = tempList
                print("list reset")
        else: 
            print("new needed: no list yet")
            newNeeded = True
      
    #3.1 if we need a new list get data from overpass using #1
    if newNeeded:
        try:
            result, lat, lon, startid = rt.overpassQuery(data['mileage'], lat, lon, data['direction'])
        except Exception as e:
            print("Error:", e)
            print("Failed to query Overpass")
            return e

        try:
            orderedResult = OrderedDict(result)
        except Exception as e:
            print("Error ", e)
            print("Element was not a dict, cannot continue the algorithm")
            return result
        
        #3.2 create the adjacency list and corresponding coordinate array which has latitude and longitude
        print("Creating Adjacency List")
        adjList, coordArray = rt.endpointList(orderedResult)

        lat, lon, startid = rt.findCheckStart(lat, lon, 1600, adjList)
        #3.3 add the new adjacency list to mongo, replacing the old list and add the TTL date for the element
        try:
            if not data["email"]:
                print("Mongo Query: Add")
                rdb.addAdjList(data["email"], adjList, [lat, lon], float(data["mileage"])/2.0, coordArray, startid, data["direction"])
            else:
                print("Mongo Query: update full")
                rdb.updateAdjListFull(data["email"], adjList, [lat, lon], float(data["mileage"])/2.0, coordArray, startid, data["direction"])
        except Exception as e:
            print("Error adding Element, check the form data")
            print(e)

    if (not newNeeded):
        coordArray = json.loads(existingList["coordArray"])
        startid = existingList["startid"]


    routes = []
    parallelDist = []    
    processes = []
    Q = Manager().Queue()
    for x in range(0, 5):
        newP = Process(target=findRoutes, args=(Q,data, lat, lon, startid, adjList, coordArray))
        newP.start()
        processes.append(newP)

    for process in processes:
        process.join()

    counter = 0
    while not Q.empty():
        result = Q.get()
        #if len(result[0]) != 0:
        routes.append(result[0])
        parallelDist.append(result[1])


    #5 return routes
    coordListPath = []
    print("",file=open('output.txt', 'w'))
    counter = 0
    for route in routes:
        coordListPath.append({"route":[]})
        for nodeId in route[0]:
            coordListPath[counter]["route"].append([coordArray[str(nodeId)]["lon"],coordArray[str(nodeId)]["lat"]])
            print('{},{},red,square,"Pune"'.format(coordArray[str(nodeId)]["lat"],coordArray[str(nodeId)]["lon"]), file=open('output.txt', 'a'))
        counter+=1
        print('\n\nNew Route\n\n', file=open('output.txt', 'a'))
    return jsonify({"coordinates": coordListPath, "length": parallelDist})


def findRoutes(Q, data, lat, lon, startid, adjList, coordArray):
    TOL = 1
    distance = 0
    totalPath = []
    routes = []
    parallelDist = []
    numRoutes = 0
    numAttempts = 10
    while (abs(distance-int(data["mileage"])) > TOL and numRoutes < numAttempts) or (numRoutes < numAttempts and int(data["mileage"]) == 1):
        #4 find one route for now, but I would like maybe 4-5 per user request (send to algorithm in this step)
        distance = 0
        if int(data["mileage"]) < 4: #Square
            checkpoints = rt.rectCheckPoints(int(data["mileage"]), data['direction'], lat, lon, startid, adjList)
        else : #circular
            checkpoints = rt.findCheckPoints(int(data["mileage"]), data['direction'], lat, lon, startid, adjList)
        try:
            G = rt.generateDataForOutput(adjList, coordArray)
            fig, ax = plt.subplots(figsize=(9, 7))
            plt.subplots_adjust(bottom=0.1, right=2, top=0.9, left=0.1)
            nx.draw(G, nx.get_node_attributes(G, 'pos'), with_labels=False, node_size=2)
            #plt.show()

            print("",file=open('output.txt', 'w'))
            for check in checkpoints:
                print('{},{},red,square,"Pune"'.format(check[0], check[1]),file=open('output.txt', 'a'))
            print("\n\n\n\n\n\n",file=open('output.txt', 'a'))
            try:
                for coord, coord2 in pairwise(checkpoints):
                    
                    path = nx.astar_path(G, str(coord[2]), str(coord2[2]), heuristic=lambda c, g: rt.xTaxiCabHeuristic(G, c, g) ,weight='weight')
                    
                    totalPath+=path
                    distance+=nx.astar_path_length(G, str(coord[2]), str(coord2[2]), heuristic=lambda c, g: rt.xTaxiCabHeuristic(G, c, g), weight='weight')

                if abs(distance-int(data["mileage"])) < TOL or (numRoutes == numAttempts-1 and len(routes) == 0):
                    routes.append(totalPath)
                    parallelDist.append(distance)
                totalPath = []            
                numRoutes+=1        
            except Exception as exc:
                print("Error: ", exc)
                print("Checkpoints failed, no path found")
                print(traceback.print_exc())
            
        except Exception as e:
            print("Error: ", e)
            print("Coordinate List Issue")
    
    Q.put((routes, parallelDist))