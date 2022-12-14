# EnergyPlus (EP) Multiple Simulations

Base setup to run multiple energy plus simulations using co-simulation platform (UCEF).  Works for a predetermined number of simulations. 

## Status

#### 2022-12-14 (Brian)

Scalability achieved! I had to rebuild the federation from the ground up, using a single-federate architecture (as opposed to transfer between Socket <--> Controller), then completely refactoring the code to remove lots of bugs created over years of development. I now have a clean, working system, under the new project [UCEF Supercontroller](https://github.com/SCU-Smart-Grid-CPS/UCEF-Supercontroller). The EP Multiple Sims code is now obsolete and moved to "archive" status.

#### 2022-05-20 (Brian)

Converting the Socket federate to handle any number of EnergyPlus simulations has been more challenging than anticipated. There is a work in progress version under ["Supersocket"](https://github.com/SCU-Smart-Grid-CPS/ep-multiple-sims/tree/supersocket) but it currently transmits data incorrectly. 

#### 2021-06-21 (Kaleb)

Currently, we need to create a new UCEF project for each number of energy plus simulations, but I am working to change the Socket federate to accept multiple energy plus simulations so that we only need one version.
