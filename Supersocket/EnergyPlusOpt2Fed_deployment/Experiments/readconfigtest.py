#  readconfigtest.py
#  
#  Author(s):   Brian Woo-Shem
#  Updated:     2021-08-24
#  Version:     0.4

# Crude attempt at a config file. For testing only

# Read xml format file

# Returns index of beginning and end of the string between a pair of tags
# file as string, initial limit index to begin search, key
def getxmlrange(f,il,key):
    keystart = "<"+key+">"
    keyend = "</"+key+">"
    indstart = f.find(keystart,il)
    indend = f.find(keyend,indstart)
    return [indstart+len(keystart),indend]


#Open file as read-only
configfile = open("tempPredictCoeff.txt","r")
f = configfile.read()

#Location name
locname = 'SF'

#Mode. "cool", "c", "heat", "h" all work
mode = "cool"

# print(f)
# print("Tenth item")
# print(f[10])

print("Find location name in file")
nameind = f.find("<name>"+locname+"</name>")
print(nameind)

#Get range for heating or cooling for this location.
moderange = getxmlrange(f,nameind,mode[0])


C_i = [-99,-99,-99]
ckey = ['c1','c2','c3'] #['<c1>','<c2>','<c3>','</c1>','</c2>','</c3>']
for ind in range (0,3): # Want 1, 2, 3
    print("Looking for: ", ckey[ind])
    a,b = getxmlrange(f,moderange[0],ckey[ind])
    try: C_i[ind] = float(f[a:b])
    except ValueError: print("Not valid: ", f[a:b])

c1, c2, c3 = C_i
print("Got C_1 = ",c1,"    C_2 = ",c2,"    C_3 = ",c3)
