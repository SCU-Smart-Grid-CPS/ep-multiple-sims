# occTo5min.py
# Author(s):    Brian Woo-Shem
# Version:      0.10 BETA
# Last Updated: 2021-11-05

import pandas as pd

def create5min():
    #If np compatible 5min csv occupancy data does not exist yet, create it using old pd method - Large block of code for error handling for forgetful homo sapiens
    #Initialize dataframe and read occupancy info 
    occupancy_df = pd.read_csv('occupancy_1hr.csv')
    occupancy_df = occupancy_df.set_index('Dates/Times')
    occupancy_df.index = pd.to_datetime(occupancy_df.index)
    # Resample using linear interpolation and export result
    occ_prob_df = occupancy_df.Probability.resample('5min').interpolate(method='linear')
    occ_prob_file = 'occupancy_probability_5min.csv'
    occ_prob_df.to_csv(occ_prob_file, header=True)
    # Resample with copy beginning of hour value and export result
    occ_stat_df = occupancy_df.Occupancy.resample('5min').pad()
    occ_stat_file = 'occupancy_status_5min.csv'
    occ_stat_df.to_csv(occ_stat_file, header=True)
    
