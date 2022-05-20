# energyOptTset2hr.py
# Author(s):    PJ McCurdy, Kaleb Pattawi, Brian Woo-Shem
# Version:      5.58 BETA
# Last Updated: 2022-05-09
# Summary:      Up to date version with debugging work to validate optimizer, energy version. Some development & patches are backported from electric version.
# Changelog:
# - Backport critical bugfix for optimizer temp prediction
# - Swapped the - sign on -cc for heating case. 
# - Added debugging noHVAC
# - Out of Bounds Temperature catches both sides of the bounds, fixed 12th timestep missing (array indexing) bug & more debug outputs & more efficient loops
# - Has c4: Diffuse Solar Radiation component!
# - NOT compatible with ANY Older Versions! Legacy no longer supported!
# - Pandas fully removed; saves 5~30s to not use any dataframes vs original code
# - Added switching for adaptive vs fixed vs occupancy control - Brian
# - Added occupancy optimization - Brian
# - Fixed optimizer should not know occupancy status beyond current time
# - Merging 2 codes, keeping the best characteristics of each
# - Base code is energyOptTset2hr_kaleb.py, with stuff from the PJ/Brian one added
# - Runtime reduced from 2.0 +/- 0.5s to 1.0 +/- 0.5s by moving mode-specific steps inside "if" statements
#   and taking dataframe subsets before transformation to matrices
# - Both heat and cool modes can optimize without out of bounds errors.
# - Fixed off by one error where indoor temperature prediction is one step behind energy, causing it to miss some energy savings.
# Usage:
#   Typically run from Controller.java in UCEF energyPlusOpt2Fed or EP_MultipleSims. Will be run again for every hour of simulation.
#   For debugging, can run as python script. In folder where this is stored:
#   ~$ python energyOptTset2hr.py [Parameters - See "ACCEPT INPUT PARAMETERS" section] 
#   Check constants & parameters denoted by ===> IMPORTANT <===

# Import Packages ---------------------------------------------------------------
from cvxopt import matrix, solvers
from cvxopt.modeling import op, dot, variable
import time
#import pandas as pd
import numpy as np
import sys
from scipy.stats import norm
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
#   'adaptive90': optimization with adaptive setpoints where 90% people are comfortable. No occupancy
#   'fixed': optimization with fixed setpoints. No occupany.
#   'occupancy_preschedule': Optimize if occupancy status for entire prediction period (2 hrs into future) is known, such as if people follow preset schedule.
MODE = 'occupancy'

# ===> Human Readable Output (HRO) SETTING <===
# Extra outputs when testing manually in python or terminal
# These may not be recognized by UCEF Controller.java so HRO = False when running full simulations
HRO = False

# Debug Setting - For developers debugging the b, D, AA, ineq matrices, leave false if you have no idea what this means
HRO_DEBUG = False

# Print HRO Header
if HRO:
    import datetime as datetime
    print()
    print('=========== energyOptTset2hr.py V5.5 ===========')
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

# Get data from csv files ------------------------------------------------------
startdat = (block-1)*12

# Use GetWeatherSolar output
# Filename format is  WeatherSolar_SF_2020-01-01_2020-01-07.csv
wfile = "WeatherSolar_" + loc + "_" + date_range + ".csv"
if loc == "default": wfile="GetWeatherSolar.csv" # Catch for older versions
# Contains [date/time, outdoor temp, humidity, solar radiation]. Need 1 and 3.

outtempnp = np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=1)
temp_outdoor = matrix(outtempnp)

solarrad= np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=3)
q_sol_direct = matrix(solarrad)

solardiffuse = np.genfromtxt(wfile, skip_header=startdat+1, max_rows=n, delimiter=',', usecols=4)
q_sol_diffuse = matrix(solarrad)

#Get wholesale data matching date range and type
if 'd' in wholesaleType.lower(): pfile = "WholesaleDayAhead_" + date_range + ".csv"
else: pfile = "WholesaleRealTime_" + date_range + ".csv"
wholesale= np.genfromtxt(pfile, skip_header=startdat, max_rows=n, delimiter=',')
cc = matrix(wholesale)*PRICING_MULTIPLIER/1000+PRICING_OFFSET



# Compute Adaptive Setpoints ---------------------------------------------------------------
# OK to remove if MODE != 'fixed' on your personal version only if the fixed mode is never used. Keep in master
if MODE != 'fixed':
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
        adaptiveCool = matrix(adc90)
        
        # Adaptive heating setpoint
        adh90[i]= outtempnp[i]*0.31 + 15.8
        if adh90[i] > HEAT_TEMP_MAX_90: adh90[i] = HEAT_TEMP_MAX_90
        elif adh90[i] < HEAT_TEMP_MIN_90: adh90[i] = HEAT_TEMP_MIN_90
        adaptiveHeat = matrix(adh90)
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
    
    if MODE != 'occupancy_sensor':
        # New np method, for getWholesaleCAISO and GetWeatherSolar, fast
        adc100 = np.zeros(n)
        adh100 = np.zeros(n)
        for i in range(0,len(outtempnp)):
            adc100[i]=outtempnp[i]*0.31 + 19.8
            if adc100[i] > COOL_TEMP_MAX_100: adc100[i] = COOL_TEMP_MAX_100
            elif adc100[i] < COOL_TEMP_MIN_100: adc100[i] = COOL_TEMP_MIN_100
            adaptive_cooling_100 = matrix(adc100)
            
            adh100[i]= outtempnp[i]*0.31 + 15.8
            if adh100[i] > HEAT_TEMP_MAX_100: adh100[i] = HEAT_TEMP_MAX_100
            elif adh100[i] < HEAT_TEMP_MIN_100: adh100[i] = HEAT_TEMP_MIN_100
            adaptive_heating_100 = matrix(adh100)
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
        
        probHeat = adaptive_heating_100-op_comfort_range
        probCool = adaptive_cooling_100+op_comfort_range
        

#------------------------ Data Ready! -------------------------

# Optimization Setup ----------------------------------------------------------

# Initialize cost
cost = 0

# setting up optimization to minimize energy times price
x=variable(n)    # x is energy usage that we are predicting (Same as E_used on PJs thesis, page 26)

# A matrix is coefficients of energy used variables in constraint equations (same as D in PJs thesis, page 26)
AA = matrix(0.0, (n*2,n))
k = 0
while k<n:
    #This solves dt*C_2 (PJ eq 2.14)
    j = 2*k
    AA[j,k] = timestep*c2
    AA[j+1,k] = -timestep*c2
    k=k+1
k=0
while k<n:
    j=2*k+2
    #Solve C_1 part. row_above * timestep * c1 * 
    while j<2*n-1:
        AA[j,k] = AA[j-2,k]*-timestep*c1+ AA[j-2,k]
        AA[j+1,k] = AA[j-1,k]*-timestep*c1+ AA[j-1,k]
        j+=2
    k=k+1

# Set signs on heat and cool energy ----------------------
# energy is positive for heating
heat_positive = matrix(0.0, (n,n))
i = 0
while i<n:
    # heat_positive is a diagonal matrix with -1 on diagonal. will get multiplied by energy x, which is positive
    heat_positive[i,i] = -1.0 # setting boundary condition: Energy used at each timestep must be greater than 0
    i +=1
    
# energy is negative for cooling
cool_negative = matrix(0.0, (n,n))
i = 0
while i<n:
    # cool_negative is a diagonal matrix with 1 on diagonal. will get multiplied by energy x, which is negative
    cool_negative[i,i] = 1.0 # setting boundary condition: Energy used at each timestep must be less than 0
    i +=1

# Right hand of equality 1 x n matrix of zeros
d = matrix(0.0, (n,1))

# inequality constraints - diagonal matrices <= 0 ------------
heatineq = (heat_positive*x<=d)
#           negative * positive <= [0]
coolineq = (cool_negative*x<=d)
#           positive * negative <= [0]

# Max heating and cooling capacity of HVAC system ------------
energyLimit = matrix(0.25, (n,1)) # .4, 0.25 before
# enforce |E_used[n]| <= E_max
heatlimiteq = (heat_positive * x <= energyLimit)
coollimiteq = (cool_negative * x <= energyLimit)


# creating S matrix to make b matrix simpler -----------------
S = matrix(0.0, (n,1))
S[0,0] = timestep*(c1*(temp_outdoor[0]-temp_indoor_initial)+c3*q_sol_direct[0]+c4*q_sol_diffuse[0])+temp_indoor_initial

#Loop to solve all S^(n) for n > 1. Each S value represents the predicted indoor temp at that time
#  based on predictions for previous timestep
i=1
while i<n:
    S[i,0] = timestep*(c1*(temp_outdoor[i]-S[i-1,0])+c3*q_sol_direct[i]+c4*q_sol_diffuse[i])+S[i-1,0]
    i+=1

# b matrix ----------------------------------------------------
# b matrix is constant term in constaint equations, containing temperature bounds
b = matrix(0.0, (n*2,1))
# Initialize counter 
k = 0
# Initialize matrices for cool and heat setpoints with flag values. These will contain setpoints for the selected MODE.
spCool = matrix(-999.9, (n,1))
spHeat = matrix(-999.9, (n,1))

# Temperature bounds for b matrix depend on MODE ---------------------------------------------- 
# Loop structure is designed to reduce unnecessary computation and speed up program.  
# Once an "if" or "elif" on that level is true, later "elif"s are ignored.
# This outer occupancy if helps when running adaptive or fixed. If only running occupancy, can remove
# outer if statement and untab the inner ones on your personal copy only. Please keep in master.
if 'occupancy' in MODE:
    # Occupany with both sensor and probability
    if MODE == 'occupancy': #For speed, putting this one first because it is the most common.
        # String for displaying occupancy status
        occnow = ''
        # If occupancy is initially true (occupied)
        if occupancy_status[0] == 1:
            occnow = 'OCCUPIED'
            # Do for the number of timesteps where occupancy is known truth
            while k < n_occ:
                # If occupied initially, use 90% adaptive setpoint for the first occupancy timeframe
                spCool[k,0] = adaptiveCool[k,0]
                spHeat[k,0] = adaptiveHeat[k,0]
                b[2*k,0]=spCool[k]-S[k,0]
                b[2*k+1,0]=-spHeat[k]+S[k,0]
                k = k + 1
        # At this point, k = n_occ = 12 if Occupied,   k = 0 if UNoccupied at t = first_timestep
        # Assume it is UNoccupied at t > first_timestep and use the probabilistic occupancy setpoints
        while k < n:
            spCool[k,0] = probCool[k,0]
            spHeat[k,0] = probHeat[k,0]
            b[2*k,0]=spCool[k]-S[k,0]
            b[2*k+1,0]=-spHeat[k]+S[k,0]
            k = k + 1
        
    elif MODE == 'occupancy_sensor':
        occnow = ''
        # If occupancy is initially true (occupied)
        if occupancy_status[0] == 1:
            occnow = 'OCCUPIED'
            # Do for the number of timesteps where occupancy is known truth
            while k < n_occ:
                # If occupied initially, use 90% adaptive setpoint for the first occupancy frame
                spCool[k,0] = adaptiveCool[k,0]
                spHeat[k,0] = adaptiveHeat[k,0]
                b[2*k,0]=spCool[k]-S[k,0]
                b[2*k+1,0]=-spHeat[k]+S[k,0]
                k = k + 1
        # At this point, k = n_occ = 12 if Occupied,   k = 0 if UNoccupied at t = first_timestep
        # Assume it is UNoccupied at t > first_timestep and use the vacant setpoints. vacantCool and vacantHeat are scalar constants
        while k < n:
            if 'c' in heatorcool:
                spCool[k,0] = vacantCool
                spHeat[k,0] = adaptiveHeat[k,0]
                b[2*k,0]=vacantCool-S[k,0]
                b[2*k+1,0]=-adaptiveHeat[k,0]+S[k,0]
            else: 
                spHeat[k,0] = vacantHeat
                spCool[k,0] = adaptiveCool[k,0]
                b[2*k+1,0]=-vacantHeat+S[k,0]
                b[2*k,0]=adaptiveCool[k,0]-S[k,0]
            k = k + 1
    
    elif MODE == 'occupancy_prob':
        occnow = 'UNKNOWN'
        spCool = probCool
        spHeat = probHeat
        while k<n:
            b[2*k,0]=probCool[k,0]-S[k,0]
            b[2*k+1,0]=-probHeat[k,0]+S[k,0]
            k=k+1
    
    # Infrequently used, so put last for speed
    elif MODE == 'occupancy_preschedule':
        # Create occupancy table
        occnow = '\nTIME\t STATUS \n'
        while k<n:
            # If occupied, use 90% adaptive setpoint
            if occupancy_status[k] == 1:
                spCool[k,0] = adaptiveCool[k,0]
                spHeat[k,0] = adaptiveHeat[k,0]
                b[2*k,0]=adaptiveCool[k,0]-S[k,0]
                b[2*k+1,0]=-adaptiveHeat[k,0]+S[k,0]
                occnow = occnow + str(k) + '\t OCCUPIED\n'
            else: # not occupied, so use probabilistic occupancy setpoints
                spCool[k,0] = vacantCool
                spHeat[k,0] = vacantHeat
                b[2*k,0]=vacantCool-S[k,0]
                b[2*k+1,0]=-vacantHeat+S[k,0]
                occnow = occnow + str(k) + '\t VACANT\n'
            k=k+1
        occnow = occnow + '\n'
    
# Adaptive setpoint without occupancy
elif MODE == 'adaptive90':
    occnow = 'UNKNOWN'
    spCool = adaptiveCool
    spHeat = adaptiveHeat
    while k<n:
        b[2*k,0]=adaptiveCool[k,0]-S[k,0]
        b[2*k+1,0]=-adaptiveHeat[k,0]+S[k,0]
        k=k+1

# Fixed setpoints - infrequently used, so put last
elif MODE == 'fixed':
    # Fixed setpoints:
    FIXED_UPPER = 23.0
    FIXED_LOWER = 20.0
    occnow = 'UNKNOWN'
    while k<n:
        spCool[k,0] = FIXED_UPPER
        spHeat[k,0] = FIXED_LOWER
        b[2*k,0]=FIXED_UPPER-S[k,0]
        b[2*k+1,0]=-FIXED_LOWER+S[k,0]
        k=k+1

elif MODE == 'noHVAC':
    # Fixed setpoints:
    FIXED_UPPER = 50.0
    FIXED_LOWER = 1.0
    occnow = 'UNKNOWN'
    while k<n:
        spCool[k,0] = FIXED_UPPER
        spHeat[k,0] = FIXED_LOWER
        b[2*k,0]=FIXED_UPPER-S[k,0]
        b[2*k+1,0]=-FIXED_LOWER+S[k,0]
        k=k+1

# Human readable output -------------------------------------------------
if HRO:
    print('MODE = ' + MODE)
    print('Date Range: ' + date_range)
    print('Day = ' + str(day))
    print('Block = ' + str(block))
    print('Initial Temperature Inside = ' + str(temp_indoor_initial) + ' °C')
    print('Initial Temperature Outdoors = ' + str(temp_outdoor[0,0]) + ' °C')
    print('Initial Max Temp = ' + str(spCool[0,0]) + ' °C')
    print('Initial Min Temp = ' + str(spHeat[0,0]) + ' °C\n')
    if occnow == '':
        occnow = 'VACANT'
    print('Current Occupancy Status: ' + occnow)
    # Detect invalid MODE before it breaks optimizer
    if spCool[1,0] == 999.9 or spHeat[1,0] == 999.9:
        print('FATAL ERROR: Invalid mode, check \'MODE\' string. \n\nx x\n >\n ⁔\n')
        print('@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING - ERROR')
        print('================================================\n')
        exit()
    print('\nCVXOPT Output: ')

# Final Optimization -------------------------------------------------------------
# Solve for energy at each timestep. Constraint D*E^n_used <= b, D = AA, E^n_used = x
# Cool (rows 0, 2, 4, ...)  D_m[n] * E_used[n] <= T_comfort,upper[n] - S[n]
# Heat (rows 1, 3, 5, ...)  D_m[n] * E_used[n] <= -T_comfort,lower[n] + S[n]
# where  b = T_comfort,upper[n] - S[n] for cooling, or b = -T_comfort,lower[n] + S[n] for heating
ineq = (AA*x <= b)

# For debug only
if HRO_DEBUG:
    print('Before opt \nS = \n')
    print(S)
    print('\nb = \n')
    print(b)
    print('\nMatrix x = \n')
    print(x)
    print('\nMatrix AA = \n')
    print(AA)
    print (ineq)

#Solve comfort zone inequalities 
if 'h' in heatorcool: # PJ eq 2.8; T^(n)_indoor >= T^(n)_comfort,lower
    # Minimize price cc, energy x, with constraint ineq
    lp2 = op(dot(cc,x),ineq) # CHANGED THIS BETWEEN 5-55 AND 5-56- DID NOT WORK, REVERTING
    op.addconstraint(lp2, heatineq)
    op.addconstraint(lp2,heatlimiteq)
elif 'c' in heatorcool:  # PJ eq 2.9; T^(n)_indoor =< T^(n)_comfort,lower
    lp2 = op(dot(-cc,x),ineq) #maybe because x is negative and price is positive?
    op.addconstraint(lp2, coolineq)
    op.addconstraint(lp2,coollimiteq)
else: #Detect if heat or cool setting is wrong before entering optimizer with invalid data
    print('\nFATAL ERROR: Invalid heat or cool setting, check \'heatorcool\' string. \n\nx x\n >\n ⁔\n')
    print('@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING - ERROR')
    print('================================================\n')
    exit()
#Tells CVXOPT to solve the problem
lp2.solve()
# ---------------------- * Solved * -----------------------------

#Initialize for either case
energy = matrix(0.00, (n,1))
temp_indoor = matrix(0.0, (n,1))

# Catch Primal Infeasibility -------------------------------------------------------
# If primal infeasibility is found (initial temperature is outside of comfort zone, 
# so optimization cannot be done within constriants), set the energy usage to 0 and 
# the predicted indoor temperature to the comfort region bound.
if x.value == None:
    # Warning message
    if HRO: print('\nWARNING: Temperature exceeds bounds, resorting to unoptimized default\n')
    
    #Set energy consumption = 0 because it is not used in this case
    energy = matrix(0.00, (n,1))
    print('energy consumption')
    j=0
    while j<nt:
        print(energy[j])
        j = j+1
    # Reset indoor temperature setting to defaults
    #temp_indoor = matrix(0.0, (n,1))
    if 'c' in heatorcool: temp_indoor = spCool
    else: temp_indoor = spHeat
    
    # Print output is read by Controller.java. Expect to send 12 of each value
    print('indoor temp prediction')
    j = 0
    while j<nt:
        print(temp_indoor[j,0])
        j = j+1
    
    print('pricing per timestep')
    j = 0
    while j<nt:
        print(cc[j,0])
        j = j+1
    
    print('outdoor temp')
    j = 0
    while j<nt:
        print(temp_outdoor[j,0])
        j = j+1
    
    print('solar radiation')
    j = 0
    while j<nt:
        print(q_sol_direct[j,0])
        j = j+1

    print('heating min')
    j = 0
    while j<nt:
        print(spHeat[j,0])
        j=j+1

    print('cooling max')
    j = 0
    while j<nt:
        print(spCool[j,0])
        j=j+1

    if HRO:
        print('diffuse solar radiation')
        j = 0
        while j<nt:
            print(q_sol_diffuse[j,0])
            j = j+1
        
        # Human-readable footer
        print('\n@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING WITHOUT OPTIMIZATION')
        print('================================================\n')
    quit() # Stop entire program

# IF Optimization Successful -------------------------------------------------------

# Energy projection is output of CVXOPT
energy = x.value

if MODE == 'noHVAC': energy = matrix(0.0, (n,1))
#print(energy)

# Compute optimized predicted future indoor temperature ----------------------------
# Critical bug in optimizer temperature prediction: Constant was swapped in temperature indoors equation for first timestep such that it solved c1(T_out - 0) instead of c1(T_out - T_in)
#temp_indoor[0,0] = timestep*(c1*(temp_outdoor[0,0]-temp_indoor[0,0])+c2*energy[0,0]+c3*q_sol_direct[0,0]+c4*q_sol_diffuse[0,0]) + temp_indoor_initial
temp_indoor[0,0] = timestep*(c1*(temp_outdoor[0,0]-temp_indoor_initial)+c2*energy[0,0]+c3*q_sol_direct[0,0]+c4*q_sol_diffuse[0,0]) + temp_indoor_initial

p = 1
while p<n:# Fixed bug: eqn should be previous temp_indoor[p-1,0], instead of current temp_indoor[p,0] = 0   (2021-11-23)
    temp_indoor[p,0] = timestep*(c1*(temp_outdoor[p,0]-temp_indoor[p-1,0])+c2*energy[p,0]+c3*q_sol_direct[p,0]+c4*q_sol_diffuse[p,0])+temp_indoor[p-1,0]
    p = p+1
#temp_indoor = temp_indoor[1:len(temp_indoor),0]
cost = cost + lp2.objective.value()    

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
if 'noHVAC' not in MODE:
    while p<n-1:
        # If energy is near zero, change setpoint to bound spHeat or spCool - Added if temp_indoor outside spCool or spHeat
        if (abs(energy[p]) < 0.0001 or temp_indoor[p] > spCool[p,0] or temp_indoor[p] < spHeat[p,0]):
            
            if HRO_DEBUG: # Temporary outputs for debugging why this isn't working
                print('Timestep: ', p, '   TempSet: ', temp_indoor[p])
                if abs(energy[p]) < 0.0001: 
                    print("Zero Energy: Predicted Energy = ", (abs(energy[p])))
                if temp_indoor[p] > spCool[p,0]: print('Too hot: Limit = ', spCool[p,0])
                if temp_indoor[p] < spHeat[p,0]: print('Too cold: Limit = ', spHeat[p,0])
            if 'c' in heatorcool: temp_indoor[p] = spCool[p,0]
            elif 'h' in heatorcool: temp_indoor[p] = spHeat[p,0]
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
    print(temp_indoor[j,0])
    j = j+1

print('pricing per timestep')
j = 0
while j<nt:
    print(cc[j,0])
    j = j+1

print('outdoor temp')
j = 0
while j<nt:
    print(temp_outdoor[j,0])
    j = j+1

print('solar radiation')
j = 0
while j<nt:
    print(q_sol_direct[j,0])
    j = j+1

print('heating min')
j = 0
while j<nt:
    print(spHeat[j,0])
    j=j+1

print('cooling max')
j = 0
while j<nt:
    print(spCool[j,0])
    j=j+1

if HRO:
    print('diffuse solar radiation')
    j = 0
    while j<nt:
        print(q_sol_diffuse[j,0])
        j = j+1
    
    # Footer for human-readable output
    print('\n@ ' + datetime.datetime.today().strftime("%Y-%m-%d_%H:%M:%S") + '   Status: TERMINATING - OPTIMIZATION SUCCESS')
    print('================================================\n')
