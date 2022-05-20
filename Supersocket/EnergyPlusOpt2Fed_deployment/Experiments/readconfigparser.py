#  readconfigparser.py
#  
#  Author(s):   Brian Woo-Shem
#  Updated:     2021-08-26
#  Version:     0.4

# trying a smarter way to read config file. For testing only.
# some parts are implemented in energyOptTset2hr.py

from configparser import ConfigParser

# opt gets from elsewhere
loc = "SF"
heatorcool = "cool"

# constant coefficients for indoor temperature equation & pricing ------------------------------
# Read config file to get constants for this simulation. Need to pass location "loc" from Controller.java
cfp = ConfigParser()
cfp.read('optCoeff.ini')
sectionName = loc + "_" + heatorcool[0] #Generate formulaic temp coeff section name
print(sectionName)
try: #For dealing with various errors if the sectionName is not found in .ini file
    try: 
        c1 = float(cfp.get(sectionName,'c1'))
        c2 = float(cfp.get(sectionName,'c2'))
        c3 = float(cfp.get(sectionName,'c3'))
        print("got c const")
        PRICING_MULTIPLIER = float(cfp.get('Pricing_Constants', 'PRICING_MULTIPLIER'))
        PRICING_OFFSET = float(cfp.get('Pricing_Constants', 'PRICING_OFFSET'))
    except (configparser.NoSectionError): c1,c2,c3 = [1,2,3]
except (NameError, ValueError): 
    # Old defaults
    c1 = 1.72*10**-5 #1.72*10**-5 #2.66*10**-5
    c2 = 7.20*10**-3 #0.0031
    c3 = 1.55*10**-7 #3.10*10**-7 #3.58*10**-7
    PRICING_MULTIPLIER = 15.0 #4.0 Changed to try to make optimization more effective (from Dr. Lee suggestion)
    PRICING_OFFSET = 0.005 #0.10
    print("Warning using defaults")

print(c1)
print(c2)
print(c3)
print(PRICING_MULTIPLIER)
print(PRICING_OFFSET)

print(type(c1))
