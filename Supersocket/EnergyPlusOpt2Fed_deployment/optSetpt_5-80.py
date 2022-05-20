# optSetpt.py
# Author(s):    Brian Woo-Shem, Kaleb Pattawi, PJ McCurdy
# Version:      5.80 BETA
# Last Updated: 2021-01-12
# Changelog:
# - Merge Kaleb's code energyOptCVXPY_0-21 with current system. THIS VERSION REPLICATES THE OLDER V5.40 CODE! NO DIFFUSE SOLAR AND USES THERMAL ENERGY
# - Temperature averaging in adaptive. Use MODE =  MODE + "_TempAvg" or "_TempInit" from Config.txt NOT backward compatible!
# - Out of Bounds Temperature catches both sides of the bounds, fixed 12th timestep missing (array indexing) bug & more debug outputs & more efficient loops
# - Add Adaptive2h debugging mode
# - Add electricity optimization
# - Added "occupancy_sensor_fixed" mode.
# - Has c4: Diffuse Solar Radiation component! (Can go back to old model by setting c4 = 0)
# - NOT compatible with ANY Older Versions! Legacy no longer supported!
# - Pandas fully removed; saves 5~30s to not use any dataframes vs original code
# - Added switching for control type, number of steps, date, location, wholesale price type - Brian
# - Added optCoeff.ini for storing indoor temp prediction and pricing coefficients. Extendable to other things as needed.
# - Added occupancy optimization - Brian
# - Runtime reduced from 2.0 +/- 0.5s to 1.0 +/- 0.5s by moving mode-specific steps inside "if" statements
#   and taking dataframe subsets before transformation to matrices
# - Fixed off by one error where indoor temperature prediction is one step behind energy, causing it to miss some energy savings.
# Usage:
#   Typically run from Controller.java in UCEF energyPlusOpt2Fed or EP_MultipleSims. Will be run again for every hour of simulation.
#   For debugging, can run as python script. In folder where this is stored:
#   ~$ python energyOptTset2hr.py [Parameters - See "ACCEPT INPUT PARAMETERS" section] 
#   Check constants & parameters denoted by ===> IMPORTANT <===

# Import Packages ---------------------------------------------------------------
#from cvxopt import matrix, solvers
#from cvxopt.modeling import op, dot, variable
import cvxpy as cp
import numpy as np
import sys
#from scipy.stats import norm
from configparser import ConfigParser

# IMPORTANT PARAMETERS TO CHANGE ------------------------------------------------

# ===> WHEN TO RUN <=== CHECK IT MATCHES EP!!!
# OR can instead designate in [PARAMETERS]
# Make sure to put in single quotes
date_range = '2020-08-01_2020-08-31' #'2020-6-29_2020-7-05' 

# Location
loc = "Default"

# Wholesale Type
# 'r' = real-time
# 'd' = day-ahead
wholesaleType = 'r'

# ===> SET HEATING VS COOLING! <===
# OR can instead designate in [PARAMETERS] -- changed so also accepts 'h' or 'c'
#   'heat': only heater, use in winter
#   'cool': only AC, use in summer
heatorcool = 'cool'

# ===> MODE <===
# OR can instead designate in [PARAMETERS]
#   'occupancy': the primary operation mode. Optimization combining probability data and current occupancy status
#   'occupancy_prob': optimization with only occupancy probability (NOT current status)
#   'occupancy_sensor': optimization with only occupancy sensor data for current occupancy status
#   'occupancy_sensor_fixed': Optimization with occupancy sensor data for current occupancy status and fixed setpoints when occupied.
#   'adaptive90': optimization with adaptive setpoints where 90% people are comfortable. No occupancy
#   'fixed': optimization with fixed setpoints. No occupany.
#   'occupancy_preschedule': Optimize if occupancy status for entire prediction period (2 hrs into future) is known, such as if people follow preset schedule.
MODE = 'occupancy'

# ===> Human Readable Output (HRO) SETTING <===
# Extra outputs when testing manually in python or terminal
# These may not be recognized by UCEF Controller.java so HRO = False when running full simulations
HRO = True

# Debug Setting - For developers debugging the b, D, AA, ineq matrices, leave false if you have no idea what this means
HRO_DEBUG = True

# Price Averaging - for cvxopt debug
priceAvg = False

# Print HRO Header
if HRO:
    import time
    import datetime as datetime
    print()
    print('=========== optSetpt.py CVXPY Edition V5.80 BETA ===========')
    print('@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: RUNNING')

# Constants that should not be changed without a good reason --------------------------------

# Temperature Data refresh rate [min]. Typical data with 5 minute intervals use 5. Hourly use 60.
temp_data_interval = 5

# Time constants. Default is set for 1 week.
n= 24 # number of timesteps within prediction windows (24 x 5-min timesteps in 2 hr window). Default - can be overwritten
nt = 12 # Number of effective predicted timesteps - Default
n_occ = 12 # number of timesteps for which occupancy data is considered known (12 = first hour for hourly occupancy data)
timestep = 5*60
# No longer used but kept for potential future use:
#days = 7
#totaltimesteps = days*12*24+3*12


# ACCEPT INPUT PARAMETERS ----------------------------------------------------------------------
# From UCEF or command line
# Run as:
#           python energyOptTset2hr.py day hour temp_indoor_initial
#           python energyOptTset2hr.py day hour temp_indoor_initial n nt
#           python energyOptTset2hr.py day hour temp_indoor_initial n nt heatorcool
#           python energyOptTset2hr.py day hour temp_indoor_initial n nt heatorcool MODE
#           python energyOptTset2hr.py day hour temp_indoor_initial n nt heatorcool MODE date_range
#           python energyOptTset2hr.py day hour temp_indoor_initial heatorcool
#           python energyOptTset2hr.py day hour temp_indoor_initial heatorcool MODE
#           python energyOptTset2hr.py day hour temp_indoor_initial heatorcool MODE date_range
#           python energyOptTset2hr.py day hour temp_indoor_initial heatorcool nt
#           python energyOptTset2hr.py day hour temp_indoor_initial n MODE
#           python energyOptTset2hr.py day hour temp_indoor_initial n MODE date_range
#   Note: Linux use 'python3' or 'python3.9' instead of 'python'
#         Windows use 'py'
# where:
#   day = [int] day number in simulation. 1 =< day =< [Number of days in simulation]
#   hour = [int] hour of the day. 12am = 0, 12pm = 11, 11pm = 23
#   temp_indoor_initial = [float] the initial indoor temperature in °C
#   n = [int] number of 5 minute timesteps to use in optimization. Requirement: n >= nt
#   nt = [int] number of 5 minute timesteps to return prediction for. Requirement: n >= nt
#   heatorcool = [str] see ===> SET HEATING VS COOLING! <===
#   MODE = [str] see ===> MODE <===
#   date_range = [str] see ===> WHEN TO RUN <===
day = int(sys.argv[1])
block = int(sys.argv[2]) +1+(day-1)*24 # block goes 0:23 (represents the hour within a day)
temp_indoor_initial = float(sys.argv[3])

# Get extra inputs, if they exist
if 4 < len(sys.argv):
    try: n = int(sys.argv[4])
    except ValueError: heatorcool = sys.argv[4]
    if 5 < len(sys.argv):
        try: nt = int(sys.argv[5])
        except ValueError: MODE = sys.argv[5]
        if 6 < len(sys.argv):
            try: # if previous one was an integer, it was n, so next one is MODE
                nt = int(sys.argv[5])
                heatorcool = sys.argv[6]
            except ValueError: date_range = sys.argv[6]
            if 7 < len(sys.argv): # Now guaranteed to be strings, per syntax rules
                MODE = sys.argv[7]
                if 8 < len(sys.argv): 
                    date_range = sys.argv[8]
                    if 9 < len(sys.argv): 
                        loc = sys.argv[9]
                        if 10 < len(sys.argv): wholesaleType = sys.argv[10]

# constant coefficients for indoor temperature equation & pricing ------------------------------
# Read config file to get constants for this simulation. Need to pass location "loc" from Controller.java
cfp = ConfigParser()
cfp.read('optCoeff.ini')
sectionName = loc + "_" + heatorcool[0] #Generate formulaic temp coeff section name
try: #For dealing with various errors if the sectionName is not found in .ini file
    try: 
        c1 = float(cfp.get(sectionName,'c1'))
        c2 = float(cfp.get(sectionName,'c2'))
        c3 = float(cfp.get(sectionName,'c3'))
        c4 = float(cfp.get(sectionName,'c4'))
        PRICING_MULTIPLIER = float(cfp.get('Pricing_Constants', 'PRICING_MULTIPLIER'))
        PRICING_OFFSET = float(cfp.get('Pricing_Constants', 'PRICING_OFFSET'))
    except (configparser.NoSectionError): c1,c2,c3,c4 = [1,2,3,4]
except (NameError, ValueError): # Old defaults
    c1 = 1.72*10**-5 #1.72*10**-5 #2.66*10**-5
    c2 = 7.20*10**-3 #0.0031
    c3 = 1.55*10**-7 #3.10*10**-7 #3.58*10**-7
    c4 = 0
    PRICING_MULTIPLIER = 15.0 #4.0 Changed to try to make optimization more effective (from Dr. Lee suggestion)
    PRICING_OFFSET = 0.005 #0.10

if HRO_DEBUG:
    print('\nGot constants: c1 = ' + str(c1) + '   c2 = ' + str(c2) + '   c3 = ' + str(c3) + '   c4 = ' + str(c4))
    print('\t Pricing Multiplier = ' + str(PRICING_MULTIPLIER))
    print('\t Pricing Offset = ' + str(PRICING_OFFSET) + '\n')

# COP constant multiplier. TODO implement into optCoeff.ini. Using 1 for now because it shouldn't impact optimization
gamma = 1

# Get data from csv files ------------------------------------------------------
startdat = (block-1)*12

# Use GetWeatherSolar output
# Filename format is  WeatherSolar_SF_2020-01-01_2020-01-07.csv
wfile = "WeatherSolar_" + loc + "_" + date_range + ".csv"
if loc == "default": wfile="GetWeatherSolar.csv" # Catch for older versions
# Contains [date/time, outdoor temp, humidity, solar radiation direct, solar rad diffuse]. Need 1, 3, 4.

outtempnp = np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
temp_outdoor = outtempnp

# Added 2022-01-02 as crude workaround for temperature constraints averaging for adaptive and occupancy setpoints to test whether it is an issue with varying constraints
# Note: temp_outdoor is used to compute the predicted temperature, and outtempnp does the adaptive/occupancy setpoints
if "_TempAvg" in MODE:
	# set outtempnp to an array of same size and type of outtempnp and fill it with the average temperature over 2hrs
	outtempnp = np.full_like(outtempnp, np.average(outtempnp))
	MODE = MODE.replace("_TempAvg", "")
# Other option to use the initial temperature at beginning of the timestep instead (less accurate but more realistic)
elif "_TempInit" in MODE:
	# set outtempnp to an array of same size and type of outtempnp and fill it with the first value (initial temp)
	outtempnp = np.full_like(outtempnp, outtempnp[0])
	MODE = MODE.replace("_TempInit", "")

solar_direct = np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=3)


solar_diffuse = np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=4)


#Get wholesale data matching date range and type
if 'd' in wholesaleType.lower(): pfile = "WholesaleDayAhead_" + date_range + ".csv"
else: pfile = "WholesaleRealTime_" + date_range + ".csv"
wholesale= np.genfromtxt(pfile, skip_header=startdat, max_rows=n, delimiter=',')
cc = wholesale*PRICING_MULTIPLIER/1000+PRICING_OFFSET

if priceAvg:
    wholesaleAvg = wholesale.mean()
    cc = np.ones(n)*wholesaleAvg*PRICING_MULTIPLIER/1000+PRICING_OFFSET


# Compute Adaptive Setpoints ---------------------------------------------------------------
# OK to remove if MODE != 'fixed' on your personal version only if the fixed mode is never used. Keep in master
if 'fixed' not in MODE:
    # Max and min for heating and cooling in adaptive setpoint control for 90% of people [°C]
    HEAT_TEMP_MAX_90 = 26.2
    HEAT_TEMP_MIN_90 = 18.9
    COOL_TEMP_MAX_90 = 30.2
    COOL_TEMP_MIN_90 = 22.9
    # Everything is already in np arrays
    adc90 = np.zeros(n)
    adh90 = np.zeros(n)
    for i in range(0,len(outtempnp)):
        # Adaptive cooling setpoint
        adc90[i]=outtempnp[i]*0.31 + 19.8
        if adc90[i] > COOL_TEMP_MAX_90: adc90[i] = COOL_TEMP_MAX_90
        elif adc90[i] < COOL_TEMP_MIN_90: adc90[i] = COOL_TEMP_MIN_90
        adaptiveCool = adc90
        
        # Adaptive heating setpoint
        adh90[i]= outtempnp[i]*0.31 + 15.8
        if adh90[i] > HEAT_TEMP_MAX_90: adh90[i] = HEAT_TEMP_MAX_90
        elif adh90[i] < HEAT_TEMP_MIN_90: adh90[i] = HEAT_TEMP_MIN_90
        adaptiveHeat = adh90
    #if HRO_DEBUG:
        #print(adaptiveCool)
        #print(adaptiveHeat)

# Get Occupancy Data & Compute Setpoints if Occupancy mode selected -------------------------
if "occupancy" in MODE:
    # Min and max temperature for heating and cooling adaptive for 100% of people [°C]
    HEAT_TEMP_MAX_100 = 25.7
    HEAT_TEMP_MIN_100 = 18.4
    COOL_TEMP_MAX_100 = 29.7
    COOL_TEMP_MIN_100 = 22.4
    # Furthest setback points allowed when building is unoccupied [°C]
    vacantCool = 32
    vacantHeat = 12
    
    # Use new np compatible 5min csv datasets. IF they don't exist, create them first - should only need to do that on first run.
    try:
        occ_prob = np.genfromtxt('occupancy_probability_5min.csv', skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
        occupancy_status = np.genfromtxt('occupancy_status_5min.csv', skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
    except OSError: #If np compatible 5min csv occupancy data does not exist yet, call another function to create it using old pd method
        from occTo5min import *
        create5min()
        occ_prob = np.genfromtxt('occupancy_probability_5min.csv', skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
        occupancy_status = np.genfromtxt('occupancy_status_5min.csv', skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
    
    if 'occupancy_sensor' not in MODE:
        # New np method, for getWholesaleCAISO and GetWeatherSolar, fast
        adc100 = np.zeros(n)
        adh100 = np.zeros(n)
        for i in range(0,len(outtempnp)):
            adc100[i]=outtempnp[i]*0.31 + 19.8
            if adc100[i] > COOL_TEMP_MAX_100: adc100[i] = COOL_TEMP_MAX_100
            elif adc100[i] < COOL_TEMP_MIN_100: adc100[i] = COOL_TEMP_MIN_100
            #adaptive_cooling_100 = adc100
            
            adh100[i]= outtempnp[i]*0.31 + 15.8
            if adh100[i] > HEAT_TEMP_MAX_100: adh100[i] = HEAT_TEMP_MAX_100
            elif adh100[i] < HEAT_TEMP_MIN_100: adh100[i] = HEAT_TEMP_MIN_100
            #adaptive_heating_100 = adh100
        #if HRO_DEBUG:
            #print(adaptive_cooling_100)
            #print(adaptive_heating_100)
        
        # Calculate Occupancy Probability comfort band
        sigma = 3.937 # This was calculated based on adaptive comfort being normally distributed
        
        # Better way to apply comfort bound function 
        fx = np.vectorize(lambda x: (1-x)/2 +1/2)
        fy = np.vectorize(lambda y: norm.ppf(y)*sigma)
        op_comfort_range = fy(fx(occ_prob))
        #print("Numpy op_comfort_range: ", op_comfort_range)
        
        probHeat = adh_100-op_comfort_range
        probCool = adc_100+op_comfort_range
        

#------------------------ Data Ready! -------------------------


# Initialize counter 
#k = 0
# Initialize matrices for cool and heat setpoints with flag values. These will contain setpoints for the selected MODE.
#spCool = np.zeros((n,1))
#spHeat = np.zeros((n,1))

# Temperature bounds for b matrix depend on MODE ---------------------------------------------- 
# Loop structure is designed to reduce unnecessary computation and speed up program.  
# Once an "if" or "elif" on that level is true, later "elif"s are ignored.
# This outer occupancy if helps when running adaptive or fixed. If only running occupancy, can remove
# outer if statement and untab the inner ones on your personal copy only. Please keep in master.
if 'occupancy' in MODE:
	# Occupany with both sensor and probability
	if MODE == 'occupancy': #For speed, putting this one first because it is the most common.
		# String for displaying occupancy status
		occnow = 'VACANT'
		# If occupancy is initially true (occupied)
		if occupancy_status[0] == 1:
			occnow = 'OCCUPIED'
			# Do for the number of timesteps where occupancy is known truth
			spCool = np.concatenate((adaptiveCool[0:n_occ,0],probCool[n_occ:n,0]))
			spHeat = np.concatenate((adaptiveHeat[0:n_occ,0],probHeat[n_occ:n,0]))

		# At this point, k = n_occ = 12 if Occupied,   k = 0 if UNoccupied at t = first_timestep
		# Assume it is UNoccupied at t > first_timestep and use the probabilistic occupancy setpoints
		else:
			spCool = probCool
			spHeat = probHeat

	elif MODE == 'occupancy_sensor':
		# String for displaying occupancy status
		occnow = 'VACANT'
		# If occupancy is initially true (occupied)
		if occupancy_status[0] == 1:
			occnow = 'OCCUPIED'
			# Do for the number of timesteps where occupancy is known truth
			spCool = np.concatenate((adaptiveCool[0:n_occ,0],vacantCool[n_occ:n,0]))
			spHeat = np.concatenate((adaptiveHeat[0:n_occ,0],vacantHeat[n_occ:n,0]))
		# At this point, k = n_occ = 12 if Occupied,   k = 0 if UNoccupied at t = first_timestep
		# Assume it is UNoccupied at t > first_timestep and use the probabilistic occupancy setpoints
		else:
			spCool = vacantCool
			spHeat = vacantHeat

	elif MODE == 'occupancy_prob':
		occnow = 'UNKNOWN'
		spCool = probCool
		spHeat = probHeat
	
	elif MODE == 'occupancy_sensor_fixed':
		occnow = 'VACANT'
		# Fixed setpoints:
		FIXED_UPPER = 23.0
		FIXED_LOWER = 20.0
		# If occupancy is initially true (occupied)
		if occupancy_status[0] == 1:
			occnow = 'OCCUPIED'
			# Do for the number of timesteps where occupancy is known truth
			spCool = np.concatenate((FIXED_UPPER[0:n_occ,0],vacantCool[n_occ:n,0]))
			spHeat = np.concatenate((FIXED_LOWER[0:n_occ,0],vacantHeat[n_occ:n,0]))
		# At this point, k = n_occ = 12 if Occupied,   k = 0 if UNoccupied at t = first_timestep
		# Assume it is UNoccupied at t > first_timestep and use the probabilistic occupancy setpoints
		else:
			spCool = vacantCool
			spHeat = vacantHeat
	
	elif MODE == 'occupancy_preschedule':
		# Create occupancy table
		occnow = '\nTIME\t STATUS \n'
		spCool = np.zeros((n,1))
		spHeat = np.zeros((n,1))
		k = 0
		while k<n:
			# If occupied, use 90% adaptive setpoint
			if occupancy_status[k] == 1:
				spCool[k,0] = adaptiveCool[k,0]
				spHeat[k,0] = adaptiveHeat[k,0]
				occnow = occnow + str(k) + '\t OCCUPIED\n'
			else: # not occupied, so use probabilistic occupancy setpoints
				spCool[k,0] = vacantCool
				spHeat[k,0] = vacantHeat
				occnow = occnow + str(k) + '\t VACANT\n'
			k=k+1
		occnow = occnow + '\n'

# Adaptive setpoint without occupancy
elif MODE == 'adaptive90':
    occnow = 'UNKNOWN'
    spCool = adaptiveCool
    spHeat = adaptiveHeat

# Fixed setpoints
elif MODE == 'fixed':
	# Fixed setpoints:
	FIXED_UPPER = 23.0
	FIXED_LOWER = 20.0
	occnow = 'UNKNOWN'
	spCool = np.zeros((n,1))
	spHeat = np.zeros((n,1))
	spCool.fill(FIXED_UPPER)
	spHeat.fill(FIXED_LOWER)

# Adaptive 90% Averaged over 2 hours - test mode
elif MODE == 'adaptive2h':
	occnow = 'UNKNOWN'
	adaptCoolAvg = adc90.mean()
	adaptHeatAvg = adh90.mean()
	spCool = np.zeros((n,1))
	spHeat = np.zeros((n,1))
	spCool.fill(adaptCoolAvg)
	spHeat.fill(adaptHeatAvg)


# Human readable output -------------------------------------------------
if HRO:
    print('MODE = ' + MODE)
    print('Date Range: ' + date_range)
    print('Day = ' + str(day))
    print('Block = ' + str(block))
    print('Initial Temperature Inside = ' + str(temp_indoor_initial) + ' °C')
    print('Initial Temperature Outdoors = ' + str(temp_outdoor[0]) + ' °C')
    print('Initial Max Temp = ' + str(spCool[0]) + ' °C')
    print('Initial Min Temp = ' + str(spHeat[0]) + ' °C\n')
    if occnow == '':
        occnow = 'VACANT'
    print('Current Occupancy Status: ' + occnow)
    # Detect invalid MODE before it breaks optimizer
    if spCool[1] == 999.9 or spHeat[1] == 999.9:
        print('FATAL ERROR: Invalid mode, check \'MODE\' string. \n\nx x\n >\n ⁔\n')
        print('@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING - ERROR')
        print('================================================\n')
        exit()
    print('\nCVXPY Output: ')

# Final Optimization -------------------------------------------------------------

# Determine relevant setpoints
if 'c' in heatorcool: sp = spCool
else: sp = spHeat

#Thermal comfort coefficient
C_THERMAL = 1000

A = np.zeros((n, n))
for i in range(0, n):
	A[i,i] = C_THERMAL * (timestep * c2)
	j = 0
	while j < i:
		A[i,j] = A[i-1, j] + A[i-1, j] * (-timestep * c1)
		j += 1
# creating S matrix to make b matrix simpler -----------------
S = np.zeros(n)
S[0] = timestep*(c1*(temp_outdoor[0]-temp_indoor_initial)+c3*solar_direct[0])+temp_indoor_initial
for i in range(1, n):
	S[i] = timestep*(c1*(temp_outdoor[i]-S[i-1])+c3*solar_direct[i])+S[i-1]

b = np.zeros(n)
for i in range(0, n):
	#b[i] = -spCool[i] + S[i]
	b = -sp + S

# Construct the problem.
E = cp.Variable(n)
objective = cp.Minimize(cp.sum(cc@E + cp.maximum(A@E + b, 0)))
constraints = [E >= 0]
prob = cp.Problem(objective, constraints)
prob.solve()

# Solve COP using outdoor temperature and indoor setpoints.
# Note: ideally use predicted indoor temperature, however that creates optimization of 2 variables. Assume that the indoor temperature is close enough to the indoor setpoints.
TdiffCOP = np.zeros((n,1))
MAX_COP = 10
COP = abs(1 / (1- temp_outdoor / sp))

# Elementwise minimum to make sure COP does not exceed limit
COP = np.minimum(COP,MAX_COP)

#Human readable equation: TdiffCOP = (T_req - T_nat) / (COP * gamma)
TdiffCOP = b / (COP * gamma)


# ---------------------- * Solved * -----------------------------

#Initialize for either case
energy = np.zeros((n,1))
temp_indoor = np.zeros((n,1))

# Catch Primal Infeasibility -------------------------------------------------------
# If primal infeasibility is found (initial temperature is outside of comfort zone, 
# so optimization cannot be done within constriants), set the energy usage to 0 and 
# the predicted indoor temperature to the comfort region bound.
# if E.value == None:
    # # Warning message
    # if HRO: print('\nWARNING: Temperature exceeds bounds, resorting to unoptimized default\n')
    
    # #Set energy consumption = 0 because it is not used in this case
    # print('energy consumption')
    # j=0
    # while j<nt:
        # print(energy[j])
        # j = j+1
    # # Reset indoor temperature setting to defaults
    # temp_indoor = sp
    
    # # Print output is read by Controller.java. Expect to send 12 of each value
    # print('indoor temp prediction')
    # j = 0
    # while j<nt:
        # print(temp_indoor[j,0])
        # j = j+1
    
    # print('pricing per timestep')
    # j = 0
    # while j<nt:
        # print(cc[j,0])
        # j = j+1
    
    # print('outdoor temp')
    # j = 0
    # while j<nt:
        # print(temp_outdoor[j,0])
        # j = j+1
    
    # print('solar radiation')
    # j = 0
    # while j<nt:
        # print(solar_direct[j,0])
        # j = j+1

    # print('heating min')
    # j = 0
    # while j<nt:
        # print(spHeat[j,0])
        # j=j+1

    # print('cooling max')
    # j = 0
    # while j<nt:
        # print(spCool[j,0])
        # j=j+1

    # if HRO:
        # print('diffuse solar radiation')
        # j = 0
        # while j<nt:
            # print(solar_diffuse[j,0])
            # j = j+1
        
        # # Human-readable footer
        # print('\n@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING WITHOUT OPTIMIZATION')
        # print('================================================\n')
    # quit() # Stop entire program

# IF Optimization Successful -------------------------------------------------------

# Compute optimized predicted future indoor temperature ----------------------------
energy = E.value
temp_indoor = np.zeros((n,1))
temp_indoor[0] = timestep*(c1*(temp_outdoor[0]-temp_indoor[0])+c2*energy[0]+c3*solar_direct[0]) + temp_indoor_initial
p = 1
while p<n:
    temp_indoor[p] = timestep*(c1*(temp_outdoor[p]-temp_indoor[p-1])+c2*energy[p]+c3*solar_direct[p])+temp_indoor[p-1]
    p = p+1

# # Electric Usage projection is output of CVXOPT
# #Note electric will be negative when cooling - will be fixed in futre but for now use abs()
# #Electric is average kW drawn over that time period (5min), need to multiply by 5/60 to get kWh
# # TODO: make electric in optimizer representative of real electricity - Brian
# electric = E.value 
# energy = np.zeros((n,1))
# # Thermal energy = electric * COP
# k=0
# while k<n:
    # energy[k,0] = electric[k,0] * COP[k,0] * gamma #Want energy to be negative when cooling.
    # k+=1

# # Compute optimized predicted future indoor temperature ----------------------------
# temp_indoor[0,0] = timestep*(c1*(temp_outdoor[0,0]-temp_indoor[0,0])+c2*energy[0,0]+c3*solar_direct[0,0]+c4*solar_diffuse[0,0]) + temp_indoor_initial
# p = 1
# while p<n: # Fixed bug: eqn should be previous temp_indoor[p-1,0], instead of current temp_indoor[p,0] = 0   (2021-11-23)
    # temp_indoor[p,0] = timestep*(c1*(temp_outdoor[p,0]-temp_indoor[p-1,0])+c2*energy[p,0]+c3*solar_direct[p,0]+c4*solar_diffuse[p,0])+temp_indoor[p-1,0]
    # cost = cost + abs(electric[p] * cc[p])
    # p = p+1
 

# Zero Energy Correction ---------------------------------------------------------------- + Invalid Indoor Temp Prediction Correction
# Problem: if the indoor temperature prediction overestimates the amount of natural heating in heat setting, 
# or natural cooling in cool setting, the optimization results in zero energy consumption. But since we 
# make the setpoints equal to the estimate, EP may not have that natural change, and instead need to run 
# HVAC unnecessarily. Since the optimizer doesn't expect HVAC to run when the energy usage = 0, make the 
# setpoints = comfort zone bounds to prevent this.
if HRO_DEBUG:
    print('Initial Energy Prediction\n', energy, '\n')
    print('Indoor Temperature Prediction Before Zero Energy Correction\n', temp_indoor, '\n')
p=0
while p<n-1:
    # If energy is near zero, change setpoint to bound spHeat or spCool - Added if temp_indoor outside spCool or spHeat
    if (abs(energy[p]) < 0.0001 or temp_indoor[p] > spCool[p,0] or temp_indoor[p] < spHeat[p]):
        
        if HRO_DEBUG: # Temporary outputs for debugging why this isn't working
            print('Timestep: ', p, '   TempSet: ', temp_indoor[p])
            if abs(energy[p]) < 0.0001: 
                print("Zero Energy: Predicted Energy = ", (abs(energy[p])))
            if temp_indoor[p] > spCool[p]: print('Too hot: Limit = ', spCool[p])
            if temp_indoor[p] < spHeat[p]: print('Too cold: Limit = ', spHeat[p])
        if 'c' in heatorcool: temp_indoor[p] = spCool[p]
        elif 'h' in heatorcool: temp_indoor[p] = spHeat[p]
    p = p+1
if HRO_DEBUG:
    print('Indoor Temperature Setting After Zero Energy & Invalid Indoor Temp Prediction Correction\n', temp_indoor, '\n')

# Print output to be read by Controller.java ---------------------------------------------
# Typically send 12 of each value, representing 1 hour.
print('energy consumption')
j=0
while j<nt:
    print(energy[j])
    j = j+1

print('indoor temp prediction')
j = 0
while j<nt:
    print(temp_indoor[j])
    j = j+1

print('pricing per timestep')
j = 0
while j<n:
    print(cc[j])
    j = j+1

print('outdoor temp')
j = 0
while j<n:
    print(temp_outdoor[j])
    j = j+1

print('solar radiation')
j = 0
while j<nt:
    print(solar_direct[j])
    j = j+1

print('heating min')
j = 0
while j<n:
    print(spHeat[j])
    j=j+1

print('cooling max')
j = 0
while j<n:
    print(spCool[j])
    j=j+1

if HRO:
    # print('Electricity Consumption')
    # j = 0
    # while j<nt:
        # print(abs(electric[j]))
        # j = j+1
    
    print('diffuse solar radiation')
    j = 0
    while j<nt:
        print(solar_diffuse[j])
        j = j+1
    
    print('All Setpoints')
    j = 0
    while j<n-1:
        print(sp[j])
        j = j+1
    
    # print('Total Cost [$] = ' + str(cost) + '\n')
    
    # Footer for human-readable output
    print('\n@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING - OPTIMIZATION SUCCESS')
    print('================================================\n')