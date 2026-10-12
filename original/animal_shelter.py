from pymongo import MongoClient
from bson.objectid import ObjectId


class AnimalShelter(object):
    """CRUD operations for Animal collection in MongoDB"""
    
    def __init__(self, username, password):
        #Initializing Mongo client This helps
        #access MongoDB databases and collections
        #This is hardwired to use the aac database, animals collection, and aac user
        #Deffinition of the connection string variable are unique to the individual apporto enviornment 
        
        #connection variables
        
        
        
        HOST = 'nv-desktop-services.apporto.com'
        PORT = 32111
        DB = 'AAC'
        COL = 'animals'
        
        #Initialize Connection
        self.client = MongoClient('mongodb://%s:%s@%s:%d' % (username, password, HOST, PORT))
        self.database = self.client['%s' % (DB)]
        self.collection = self.database['%s' % (COL)]
        
    #Create Method to implement C in CRUD
    def create(self, data):
        if data is not None:
            result = self.database.animals.insert_one(data) #data should be dictionary
            return result.acknowledged
        else:
            raise Exception("Nothing to save because data parameter is empty")
    def read(self, query):
        if query is not None:
            return list(self.database.animals.find(query))
        else:
           return list(self.database.animals.find({}))
    def update(self, query, new_values):
        if query and new_values:
            result = self.database.animals.update_many(query, {"$set": new_values})
            return result.modified_count
        else:
            raise Exception("Update failed: Query or new_values param is empty")
            
    def delete(self, query):
        if query is not None:
            result = self.database.animals.delete_many(query)
            return result.deleted_count
        else:
            raise Exception("Delete failed: Query param is empty")
