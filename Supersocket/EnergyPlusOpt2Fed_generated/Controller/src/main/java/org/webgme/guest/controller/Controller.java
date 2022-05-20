/*
File:           controller.java
Project:        EnergyPlus Optimization + Occupancy + Multiple Simulations SUPERSOCKET
Author(s):      PJ McCurdy, Kaleb Pattawi, Brian Woo-Shem, Hannah Covington
Version:        5.99 BETA
Last Updated:   2022-05-20 by Brian
Notes: Code for the optimization simulations. Should compile and run but may not have perfect results.
Run:   Change file paths in this code. Then build or build-all. Run as part of federation.

*Changelog:
    * Adapted to work with SUPERSOCKET
    * 8 simulations version!!!
    * Merged Brian's v5.3 with Hannah's Appliance Scheduler v2.2
    * Requires Occupancy8Days.csv
*/

package org.webgme.guest.controller;
// Default package imports
import org.webgme.guest.controller.rti.*;
import org.cpswt.config.FederateConfig;
import org.cpswt.config.FederateConfigParser;
import org.cpswt.hla.InteractionRoot;
import org.cpswt.hla.base.AdvanceTimeRequest;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

// Added package imports
import java.io.*;
import java.net.*;
import org.cpswt.utils.CpswtUtils;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.util.Random;    // random num generator
import java.lang.*;
import java.util.*;
// For nice date labeled filenames
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

// Define the Controller type of federate for the federation.
public class Controller extends ControllerBase {
    private final static Logger log = LogManager.getLogger();

    private double currentTime = 0;

    public Controller(FederateConfig params) throws Exception {
        super(params);
    }

    // Defining Global Variables ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    // Basic IO
    static final int numSims = 2;  // CHANGE FOR MULTIPLE EP SIMS! Default is 1 for single EP. TODO: get it to read config.
    // making these things static final so they don't get modified by mistake.
    String[] varNames = new String[12*numSims];   // add more empty vals if sending more vars -- Brian changed from 15 to 22
    String[] doubles = new String[12*numSims];
    String[] dataStrings = new String[numSims];
    String[] holder=new String[numSims];
    double[] outTemps=new double[numSims]; //Outdoor temp
    double[] setCool= new double[numSims]; 
    double[] setHeat= new double[numSims];
    // From EP via Socket
    double[] zoneTemps= new double[numSims];
    double[] zoneRHs= new double[numSims];
    double[] heatingEnergy= new double[numSims];
    double[] coolingEnergy= new double[numSims];
    double[] netEnergy= new double[numSims];
    double[] energyPurchased= new double[numSims];
    double[] energySurplus= new double[numSims];
    double[] solarRadiation= new double[numSims];
    double[] receivedHeatTemp= new double[numSims];
    double[] receivedCoolTemp= new double[numSims];
    double[] dayOfWeek= new double[numSims];
    double price = 10; // Set a default price here
    int[] numVars = new int[numSims]; //for multiple sockets
    
    // for no time delay
    int receivedSimsCount = 0;
    boolean receivedSocket = false;
    boolean receivedMarket = false;
    boolean receivedReader = false;
    int waitTime = 0;
    String timestep_Socket = "";
    //String timestep_Reader = "";  //UNcomment if using Reader and Market federates
    //String timestep_Market = "";
    //String timestep_Controller = "";
    
    // Initializing for Fuzzy Control
    int fuzzy_heat = 0;  // NEEDS TO BE GLOBAL VAR outside of while loop
    int fuzzy_cool = 0;  // NEEDS TO BE GLOBAL VAR outside of while loop
    
    // For Setpoints & Optimization ========================================================
    // Number of optimization to run per hour. Default is 1
    static final int nt = 12; //timesteps PER hour
    static final int nopt = 12/nt;
    // Temperature and setpoint data
    //String[] varsT = new String[nt+2]; 
    //String[] varsH = new String[nt+2]; 
    //String[] varsC = new String[nt+2];
    // Keystrings for encoding and decoding data into a string
    String varNameSeparator = "@";
    String doubleSeparator = ",";
    String configSeparator = ",";
    String optDataString = "";
    int day = 0, p = 0;
    
    String sblock = null;
    String sday = null;
    //==========================================================================
    
    //For appliance scheduling =================================================
    //input variables
    //int nt = 12; //timesteps PER hour - constant, same as for Setpoints & Optimization
	int state;
	int runTime = 12; //number of time steps the appliance is activated
	int sleepTime = 22*nt; //time that the house is asleep
	int wakeTime = 6*nt; //time that the house is awake
	int numActPerDay = 1; //number of activations 
	int numActToday = 0;
	int dayCount = 1;
	double dailyActivationProb = .59;
	double activationProb = 0;
	int numOccupiedToday = 0;
	ArrayList<Integer> occupancyData = new ArrayList<Integer>();
	ArrayList<Integer> activationHistory = new ArrayList<Integer>(); // can change to just a variable, not ann array list
	ArrayList<Integer> stateHistory = new ArrayList<Integer>();
	ArrayList<Double> randomNumHistory = new ArrayList<Double>();
	ArrayList<Integer> timeStepsOccupied = new ArrayList<Integer>();
   //============================================================================
    
    //Get date + time string for output file naming.
    // Need to do this here because otherwise the date time string might change during the simulation
    String datime = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd_HH-mm"));

    //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    private void checkReceivedSubscriptions() {
        InteractionRoot interaction = null;
        while ((interaction = getNextInteractionNoWait()) != null) {
            if (interaction instanceof Socket_Controller) {
                handleInteractionClass((Socket_Controller) interaction);
            }
            else {
                log.debug("unhandled interaction: {}", interaction.getClassName());
            }
        }
    }

// Giant method for what Controller does every timestep ____________________________
    private void execute() throws Exception {
        if(super.isLateJoiner()) {
            log.info("turning off time regulation (late joiner)");
            currentTime = super.getLBTS() - super.getLookAhead();
            super.disableTimeRegulation();
        }

        /////////////////////////////////////////////
        // TODO perform basic initialization below //
        /////////////////////////////////////////////
        
        // Read simulation settings from config.txt =============================================
        log.info("Getting Configuration Settings: ");
        File cf = new File("config.txt");
        BufferedReader br = new BufferedReader(new FileReader(cf));
        //Determine Setpoint Type --- Leave these false for config.txt input
        boolean[] optimizeSet = new boolean[numSims]; // intialize with false; //True if optimized, false if not optimized
        boolean[] adaptiveSet = new boolean[numSims]; // intialize with false; //True if using adaptive setpoint, false if fixed setpoint. Not used if optimizeSet = true.
        boolean[] occupancySet = new boolean[numSims]; // intialize with false; //Does it use occupancy?
        //Determine Setpoint Type
        String st = "";
        String[] mode = new String[numSims];
        String[] heatOrCool = new String[numSims];
        char[] hcc = new char[numSims];  //  initialize with 'z' in for loop
        String dateRange = "";
        String loc = "";
        char wholesaleType = 'z';
        String pythonCommand = "python3";
        boolean writeFile = false;
        String optimizerFile = "energyOptTset2hr.py";
        String setpointFile = "occupancyAdaptSetpoints.py";

        String temp = "";
        String[] temps = new String[numSims];
        while ((st = br.readLine())!=null){
            //System.out.println(st);
            // Mode is per-sim
            if(st.contains("MODE:")){ //Use contains so tagline can have other instructions
                temp = br.readLine();
                //log.info("Modes: " + temp);
                mode = temp.split(configSeparator);
                //log.info("Mode 1:" + mode[0]);
                //log.info("Mode 2:" + mode[1]);
                for(int j=0; j<numSims; j++){
                    System.out.println("Mode " + j + ": " + mode[j]);
                }
            }// heatorcool is per sim
            else if(st.contains("heatorcool:")){
                temp = br.readLine(); // Immutable
                //log.info("Heat or Cool vars: " + temp);
                heatOrCool = temp.split(configSeparator);
                //log.info("heatOrCool 1:" + heatOrCool[0]);
                //log.info("heatOrCool 2:" + heatOrCool[1]);
                for(int j=0; j<numSims; j++){
					int k = 0; //catch extra spaces stuck in the string
					while (heatOrCool[j].charAt(k) == ' ') { k++; }
                    hcc[j] = heatOrCool[j].charAt(k); // should be one of: h, c, a. MAY change during auto setting
                    System.out.println("hcc " + j + ": " + hcc[j]);
                    if (hcc[j] != 'h' & hcc[j] != 'c' & hcc[j] != 'a'){ System.out.println("Warning: Invalid heat or cool setting for " + j );}
                }
            }// global
            else if(st.contains("date_range:")){
                dateRange = br.readLine();
            } // per sim
            else if(st.contains("optimize:")){
                temp = br.readLine();
                temps = temp.split(configSeparator);
                if (temps.length < numSims){
					// Warn and propagate first value
					for(int j=0; j<numSims; j++){
						optimizeSet[j] = Boolean.parseBoolean(temps[0]);
						System.out.println("Warning: Insufficient Optimize info in config. Using first value for all = " + optimizeSet[0]);
					}
				}
				else{
					// sufficient values provided
					for(int j=0; j<numSims; j++){
						optimizeSet[j] = Boolean.parseBoolean(temps[j]);
						System.out.println("optimizeSet " + j + ": " + optimizeSet[j]);
					}
				}
			} // global
            else if(st.contains("location:")){
                loc = br.readLine();
            }
            else if(st.contains("wholesale_type:")){
                wholesaleType = br.readLine().charAt(0);
            }
            else if(st.contains("python_command:")){
                pythonCommand = br.readLine();
            }
            else if(st.contains("write_extra_data_files:")){
                writeFile = Boolean.parseBoolean(br.readLine());
            }
            else if(st.contains("optimizer_code_file_name:")){
				optimizerFile = br.readLine();
			}
			else if(st.contains("occupancy_adaptive_setpoints_code_file_name:")){
				setpointFile = br.readLine();
			}
        }
        //log.info("Mode: " + mode);
        //log.info("Heat or Cool: " + heatOrCool);
        System.out.println("Date Range: " + dateRange);
        //log.info("Optimize: " + optimizeSet);
        System.out.println("Location: " + loc);
        System.out.println("Wholesale Type: " + wholesaleType);
        System.out.println("writeFile: " + writeFile);
        System.out.println("pythonCommand: " + pythonCommand);
        int numPython = 0; //num sims requiring Python
        // if not optimizing, figure out occupancySet and adaptiveSet booleans. Note optimize uses only MODE, not occupancySet or adaptiveSet.
        for(int j=0; j<numSims; j++){
            // Need to intialize occupancySet and adaptiveSet = false and change if mode says otherwise:
            occupancySet[j] = false;
            adaptiveSet[j] = false;
            if(optimizeSet[j]){
				numPython++;
			}
            else{
                if(mode[j].contains("occupancy")){ 
					occupancySet[j] = true; 
					numPython++;
				}
                else if(mode[j].contains("adaptive")){ adaptiveSet[j] = true;}
                else if(mode[j].equals("")){ System.out.println("Text Alert: config.txt missing or contains invalid parameters."); }
            }
        }
        // Temperature and setpoint data
		String[] varsT = new String[numPython*nt+2]; 
		String[] varsH = new String[numPython*nt+2]; 
		String[] varsC = new String[numPython*nt+2];
		String[] varsP = new String[numPython*nt+2]; 
		String[] varsS = new String[numPython*nt+2];
		String[] varsO = new String[numPython*nt+2]; 
		String[] varsE = new String[numPython*nt+2];
        // end config.txt ================================================================
        
        // Reading Occupancy Information ==================================================
        File data = new File("Occupancy8Days.csv");
	    Scanner scanner = new Scanner(data);
	    scanner.useDelimiter(",");
	    while (scanner.hasNext()) {
		    occupancyData.add(scanner.nextInt());
	    }
	    scanner.close();
	    System.out.println("OCCUPANCY:");
	    System.out.println(occupancyData);
	
	    //getting amount of occupancy for each day
	    for (int k = 0; k<occupancyData.size(); k++) {
		    if (occupancyData.get(k) == 1) {
			    numOccupiedToday = numOccupiedToday + 1;
		    }
		    if ((k+1)%(24*nt) == 0 && k!=0) {
			    timeStepsOccupied.add(numOccupiedToday); 
			    numOccupiedToday = 0;
		    }
	    }
	    System.out.println("TIME STEPS OCCUPIED PER DAY:");
	    System.out.println(timeStepsOccupied);
	
        //end of occupancy information =================================================
        
        AdvanceTimeRequest atr = new AdvanceTimeRequest(currentTime);
        putAdvanceTimeRequest(atr);

        if(!super.isLateJoiner()) {
            log.info("waiting on readyToPopulate...");
            try{
                readyToPopulate();
            }
            catch (Exception ej){
                System.out.println("Data Explosion! Please reboot your computer and try again.");
            }
            log.info("...synchronized on readyToPopulate");
        }

        ///////////////////////////////////////////////////////////////////////
        // TODO perform initialization that depends on other federates below //
        ///////////////////////////////////////////////////////////////////////

        if(!super.isLateJoiner()) {
            log.info("waiting on readyToRun...");
            readyToRun();
            log.info("...synchronized on readyToRun");
        }

        startAdvanceTimeThread();
        log.info("started logical time progression");

        while (!exitCondition) {
            atr.requestSyncStart();
            enteredTimeGrantedState();

            ////////////////////////////////////////////////////////////
            // TODO send interactions that must be sent every logical //
            // time step below                                        //
            ////////////////////////////////////////////////////////////

            // Set the interaction's parameters.
            //
            //    Controller_Socket vController_Socket = create_Controller_Socket();
            //    vController_Socket.set_actualLogicalGenerationTime( < YOUR VALUE HERE > );
            //    vController_Socket.set_dataString( < YOUR VALUE HERE > );
            //    vController_Socket.set_federateFilter( < YOUR VALUE HERE > );
            //    vController_Socket.set_originFed( < YOUR VALUE HERE > );
            //    vController_Socket.set_simID( < YOUR VALUE HERE > );
            //    vController_Socket.set_sourceFed( < YOUR VALUE HERE > );
            //    vController_Socket.sendInteraction(getLRC(), currentTime + getLookAhead());

            System.out.println("timestep before receiving Socket/Reader: "+ currentTime);
            //log.info("timestep before receiving Socket/Reader: " + currentTime);
            // Waits to receive Socket_Controller and Reader_Controller to ensure everything stays on the same timestep
            while (!(receivedSocket)){
                //while ((!(receivedSocket) || !(receivedReader))){ // Reader stuff is commented out because Reader not currently used
                System.out.println("waiting to receive Socket_Controller interaction...\t"+waitTime/3);
                synchronized(lrc){
                    lrc.tick();
                }
                checkReceivedSubscriptions();
                if(!receivedSocket){
                    CpswtUtils.sleep(100 + waitTime);
                    waitTime+=3;
                    if(waitTime > 200){
                        System.out.println("Socket has abandoned me! I'm tired of waiting. Goodbye.");
                        System.exit(1);
                    }
                }
            /* }else if(!receivedReader){
                   log.info("waiting on Reader_Controller...");
                   CpswtUtils.sleep(100);
               }*/
            }
          receivedSocket = false;
          receivedSimsCount = 0;
          waitTime = 0;
          //receivedReader = false;
          
          // SUPERSOCKET from log, two interactions occur between previous output in this block and this output here
          System.out.println("timestep after receiving Socket/Reader and before sending to Market: "+ currentTime);
/*         // Market stuff; commented out because Market is not currently used
           // TODO send Controller_Market here! vvvvvvvv
           log.info("sending Controller_Market interaction");
           Controller_Market sendMarket = create_Controller_Market();
           sendMarket.set_dataString("");
           System.out.println("Send controller_market and Reader_Controller interaction:");
           sendMarket.sendInteraction(getLRC());

          
           log.info("waiting for Market_controller interaction...");
           // Wait to receive price from market  
           while (!receivedMarket){
               log.info("waiting to receive Market_Controller interaction...");
               synchronized(lrc){
                   lrc.tick();
               }
               checkReceivedSubscriptions();
               if(!receivedMarket){
                   log.info("waiting on Market_Controller...");
                   CpswtUtils.sleep(100);
               }
           }
           receivedMarket = false;
           log.info("received Market_controller interaction!");
           System.out.println("timestep after receiving Market: "+ currentTime);
*/


			// DO NOT do these for every EP sim! Only Once per timestep!!!
			double hour = (double) ((currentTime%288) / 12);
			System.out.println("hour is: "+hour);
			// At beginning of the day, increment day
			if (hour == 0){
				day = day+1;
			}
			
			sblock= String.valueOf((int)hour);
            sday = String.valueOf(day);
            
            // Initialize counter for setting setpoint temp for next hour  -- only reset p before anything runs
			p=0;
			System.out.println("timestep p = "+String.valueOf(p));
			
			int j = 0;

//+++++++++++++++++++++++++++++ Start loop to calculate setpoints for each EnergyPlus simulation +++++++++++++++++++++++++++++//
            for(int i=0;i<numSims;i++){

        // SETPOINTS & OPTIMIZATION ===============================================================
                // Reset variables to defaults
                String s = null;
                String dataStringOptE = "";
                String dataStringOptT = "";
                String dataStringOptP = "";
                String dataStringOptO = "";
                String dataStringOptS = "";
                String dsoHeatSet = "";
                String dsoCoolSet = "";
                
                String separatorOpt = ",";
                char var2save = 'Z'; // default value to save nothing
                String pycmd = "";
                //String autopycmd = ""; // auto heat/cool variable, disable until implementation done
                
                
                // WARNING: WORK IN PROGRESS! PYTHON PART DOES NOT WORK YET SO DON'T USE THIS MODE!
                // Automatic Heat vs Cool Selection ----------------------------------------
                // If automatic heating and cooling mode, and it is the beginning of an hour
                /*
                if (heatOrCool[i].equals("auto") && hour%nopt == 0){
                    // Run autoHeatCool.py
                    Process autorun;
                    autopycmd = pythonCommand + " ./autoHeatCool.py " + sday + " " + sblock + " " + dateRange;
                    try{
                        autorun = Runtime.getRuntime().exec(autopycmd); 
                        System.out.println("Run:  " + autopycmd); //Display command run for user debugging
                        BufferedReader aInput = new BufferedReader(new InputStreamReader(autorun.getInputStream()));

                        // Gets input data from Python. Should be single string with either "heat" or "cool"
                        // AS long as there is another output line with data,
                        while ((s = aInput.readLine()) != null) {
                            switch (s) {
                                case "heat": hcc[i] = 'h'; System.out.println("Autoselect Set to HEAT"); break;
                                case "cool": hcc[i] = 'c'; System.out.println("Autoselect Set to COOL"); break;
                                case "Traceback (most recent call last):":
                                    System.out.println("\nHiss... Python autoHeatCool.py crash detected. Try pasting command after \"Run\" in the terminal and debug Python.");
                                    System.out.println("Alert! Using heat/cool setting from previous hour, which is: " + hcc[i]);
                                    break;
                                default:
                                    System.out.println("Warning: Unexpected String from autoHeatCool.py: \n    " + s);
                            } //End switch
                            s = null;
                        } // End While
                    } // End try
                    catch (IOException e) {
                        e.printStackTrace();
                        System.out.println("\nHiss... Python crashed or failed to run. Try pasting command after \"Run\" in the terminal and debug Python."); 
                    } // End catch
                } //End run autoHeatCool -------------------------------------------------------
                * */

                // Run Python Setpoints Code ---------------------------------------------------
                if (optimizeSet[i] || occupancySet[i]){
                    // On the whole hours only, run the optimization ---------------------------
                    if (hour%nopt == 0){
                        try {
                            
                            dataStringOptE = sblock;
                            //dataStringOptT = sblock; do not reset
                            dataStringOptP = sblock;
                            dataStringOptO = sblock;
                            dataStringOptS = sblock;
                            dsoHeatSet = sblock;
                            dsoCoolSet = sblock;
                            //System.out.println("sblock: " +sblock);
                            //System.out.println("sday: " +sday);
                            //System.out.println("zonetemp string: " +String.valueOf(zoneTemps[i]));
                            Process pro;
                            
                            // Call Python optimization & occupancy code with necessary info
                            if (optimizeSet[i]){
                                pycmd = pythonCommand + " ./" + optimizerFile + " " + sday +" " +sblock +" "+ String.valueOf(zoneTemps[i])+ " " + String.valueOf(24) + " " + nt + " " + hcc[i] + " " + mode[i] + " " + dateRange + " " + loc + " " + wholesaleType; 
                            }
                            else{ // Call Python adaptive and occupancy setpoints code with necessary info
                                pycmd = pythonCommand + " ./" + setpointFile + " " +sday +" " +sblock +" "+ String.valueOf(zoneTemps[i])+ " " + String.valueOf(24) + " " + nt + " " + hcc[i] + " " + mode[i] + " " + dateRange + " " + loc + " " + wholesaleType;
                            }
                            System.out.println("Run:  " + pycmd); //Display command used for debugging
                            pro = Runtime.getRuntime().exec(pycmd); // Runs command

                            BufferedReader stdInput = new BufferedReader(new InputStreamReader(pro.getInputStream()));
                        
                        // Gets input data from Python that will either be a keystring or a variable. 
                        // AS long as there is another output line with data,
                            while ((s = stdInput.readLine()) != null) {
                                //System.out.println(s);  //for debug
                                // New nested switch-case to reduce computing time and fix so it's not appending data meant for the next one. - Brian
                                // Replaced a bunch of booleans with single key char var2save - Brian
                                // If current line is a keystring, identify it by setting the key var2save to that identity
                                switch (s) {
                                    case "energy consumption":
                                        var2save = 'E';
                                        break;
                                    case "indoor temp prediction":
                                        var2save = 'T';
                                        break;
                                    case "pricing per timestep":
                                        var2save = 'P';
                                        break;
                                    case "outdoor temp":
                                        var2save = 'O';
                                        break;
                                    case "solar radiation": 
                                        var2save = 'S'; 
                                        break;
                                    case "heating min": 
                                        var2save = 'H'; 
                                        break;
                                    case "cooling max": 
                                        var2save = 'C'; 
                                        break;
                                    case "Traceback (most recent call last):":
                                        System.out.println("\nHiss... Python crash detected. Try pasting command after \"Run\" in the terminal and debug Python.");
                                        var2save = 'Z';
                                        break;
                                    default: // Not a keystring, so it is probably data
                                        switch(var2save) {
											case 'E': dataStringOptE = dataStringOptE + separatorOpt + s; break;
											case 'T': dataStringOptT = dataStringOptT + separatorOpt + s; break;
											case 'P': dataStringOptP = dataStringOptP + separatorOpt + s; break;
											case 'O': dataStringOptO = dataStringOptO + separatorOpt + s; break;
											case 'S': dataStringOptS = dataStringOptS + separatorOpt + s; break;
											case 'H': dsoHeatSet = dsoHeatSet + separatorOpt + s; break;
											case 'C': dsoCoolSet = dsoCoolSet + separatorOpt + s; break;
											default: // Do nothing
                                        } // End var2save switch case
                                    } // End s switch case
                                } //End while next line not null
                            } // End try
                            catch (IOException e) {
                                e.printStackTrace();
                                System.out.println("\nHiss... Python crashed or failed to run. Try pasting command after \"Run\" in the terminal and debug Python."); 
                            }
                            // Extra check if no keystrings found, var2save will still be default 'Z'. Controller will probably crash after this, but it is usually caused by Python code crashing and not returning anything. Warn user so they debug correct program.
                            if (var2save == 'Z') { System.out.println("Hiss... No keystrings from Python found. Python may have crashed and returned null. Check command after \"Run:\""); }
                            
                            //Convert single datastring to array of substrings deliniated by separator
                            String[] splitE = dataStringOptE.split(separatorOpt);
                            // WARNING: varsT must be instantiated differently to save values across different sims!
                            System.out.println("Sim " + i + "   dataStringOptT = " + dataStringOptT);
                            String[] splitT = dataStringOptT.split(separatorOpt, nt+1); //2nd entry is limit to prevent overflowing preallocated array
                            String[] splitP = dataStringOptP.split(separatorOpt);
                            String[] splitO = dataStringOptO.split(separatorOpt);
                            String[] splitS = dataStringOptS.split(separatorOpt);
                            //System.out.println("dsoHeatSet = " + dsoHeatSet); //for debugging
                            //System.out.println("dsoCoolSet = " + dsooolSet);
                            String[] splitH = dsoHeatSet.split(separatorOpt, nt+1);
                            String[] splitC = dsoCoolSet.split(separatorOpt, nt+1);
                            j = 0;
                            while(j<nt){
								// for supersocket add to array of existing values
								varsE[j+i*nt] = splitE[j];
								varsT[j+i*nt] = splitT[j];
								varsO[j+i*nt] = splitO[j];
								varsS[j+i*nt] = splitS[j];
								varsH[j+i*nt] = splitH[j];
								varsC[j+i*nt] = splitC[j];
								varsP[j+i*nt] = splitP[j];
								j++;
							}
							System.out.println("Sim " + i + "   varsT = " + varsT);
                            
                            // End getting data from Python Setpoint Code ------------------------
                            
                            // Writing data to file _____________________________________________
                            if (writeFile){
								try{
									// Create new file in Deployment folder with name including description, time and date string - Brian
									// DataCVXOPT_mode_heatOrCool_YYYY-MM-DD_HH-mm.txt
									File cvxFile = new File("DataCVXOPT_"+loc + "_" +mode[i]+"_"+heatOrCool[i]+"_Sim"+i+"_"+datime+".txt");

									// If file doesn't exists, then create it
									if (!cvxFile.exists()) {
										cvxFile.createNewFile();
										// Add headers and save them - Brian
										FileWriter fw = new FileWriter(cvxFile.getAbsoluteFile(),true);
										BufferedWriter bw = new BufferedWriter(fw);
										bw.write("Energy_Consumption[J]\tIndoor_Temp_Prediction[°C]\tEnergy_Price[$]\tOutdoor_Temp[°C]\tSolarRadiation[W/m^2]\tMin_Heat_Setpt[°C]\tMax_Cool_Setpt[°C]\n");
										bw.close();
									}

									FileWriter fw = new FileWriter(cvxFile.getAbsoluteFile(),true);
									BufferedWriter bw = new BufferedWriter(fw);

									// Write in file
									for (int in =0;in<13;in++) {
										bw.write(varsE[nt*i+in]+"\t"+varsT[nt*i+in]+"\t"+varsP[nt*i+in]+"\t"+varsO[nt*i+in]+"\t"+varsS[nt*i+in]+"\t"+varsH[nt*i+in]+"\t"+varsC[nt*i+in]+"\n");
									}
									// Close and save file
									bw.close();
								}
								catch(Exception e){
									System.out.println("Text Alert: Could not write DataCVXOPT.txt file.");
									System.out.println(e);
								} 
                            }// End DataCVXOPT file ------------------------------------------------
                
                            // resetting 
                            var2save = 'Z';

                            // Initialize counter for setting setpoint temp for next hour 
                            //p=0;
                            //System.out.println("timestep p = "+String.valueOf(p));
                        } //End hourly optimization ------------------------------------------------
                        
                        // Below here runs every timestep that is not on a whole hour -------

                        try{
							// data for this round is index numSimsWithOpt*12 + p
							// nt = 12
                            if(hcc[i] == 'h'){
                                setHeat[i]=Double.parseDouble(varsT[i*nt+p+1]); //vars T has 12 elements. p should be reset per hour per sim.  
                                System.out.println("setpointHeating: "+String.valueOf(setHeat[i]));
                                setCool[i]=32.0; //Setback to prevent AC activation
                            }
                            else{ // Cooling
                                setCool[i]=Double.parseDouble(varsT[i*nt+p+1]);
                                System.out.println("setpointCooling: "+String.valueOf(setCool[i]));
                                setHeat[i]=15.0; //Setback to prevent heater activation
                            }
                        }
                        catch(ArrayIndexOutOfBoundsException aie){ //Detect if couldn't get datastrings from Python. Usually indicates Python crashed.
							System.out.println("Errorlog: Attempted to access simulation " + i);
							System.out.println("Errorlog: Attempted to access varsT " + i*nt+p+1);
                            System.out.println(aie);
                            if (p<2){ //Crashed on first few timesteps usually means Python crashed
								System.out.println("Hiss... Python may have crashed and returned null. Check command after \"Run:\" ");
							}
							else { //crashed later usually means data is not stored right
								System.out.println("Help! Stop sending me data! - Multisim data storage has some error");
							}
                            // This error activated on 8 sim, but Python is fine
                        }
                        //p=p+1;
                        //System.out.println("timestep p = "+String.valueOf(p)); //p got to 13 and crashed 
                        //Suspect that varsT may need to become 2D for timestep and sim
                        
                    //-------------------------------------------------------------------------------------------------            
                    // determine heating and cooling setpoints for each simID
                    // will eventually change this part for transactive energy

                    // Fuzzy control for Occupancy & Optimization _______________________________________________________
                    // Brian rebuilt this so fuzzy doesn't replace the expanded occupancy comfort bounds with defaults
                    double max_cool_temp, min_heat_temp; 
                    double OFFSET = 0.5; // need to change slightly higher/lower so E+ doesnt have issues
                    
                    // Determine minimum and maximum temperatures allowed from optimization or occupancy output as 'heating setpoint bounds' and 'cooling setpoint bounds' - Brian
                    min_heat_temp = Double.parseDouble(varsH[p]); // [p] because p incremented since previous usage
                    max_cool_temp = Double.parseDouble(varsC[p]);
                    // Alternately it might actually supposed to be setCool and setHeat?
                    // In which case would be identical to the non-optimized adaptive and fixed cases.

                    // Fuzzy cool and heat are global variables that toggle only when criteria is met.
                    if (hcc[i] == 'c'){ //Cooling
                        if (zoneTemps[i] >= max_cool_temp - 0.2){ // if likely to exceed maximum band
                            fuzzy_cool = -1;
                        } else if (zoneTemps[i] <= setCool[i]-1.1){ // if colder than necessary, allow to warm up
                            fuzzy_cool = 1;
                        }
                        setCool[i] = setCool[i] - 0.6 + fuzzy_cool * OFFSET;   // -0.6 so that oscillates 0.1-1.1 degree under cooling setpoint
                        setHeat[i] = 15.0; // IF COOLING for now to avoid turning on heat
                    }
                    else{ // Heating
                        if (zoneTemps[i] <= min_heat_temp + 0.2){ // if likely to exceed minimum band
                            fuzzy_heat = 1;
                        } else if (zoneTemps[i] >= setHeat[i]+1.1){
                            fuzzy_heat = -1;
                        }
                        setHeat[i] = setHeat[i] + 0.6 + fuzzy_heat * OFFSET;  // +0.6 so that oscillates 0.1-1.1 degree above heating setpoint
                        setCool[i] = 32.0; // IF HEATING for now to avoid turning on AC
                    }
                    // End fuzzy
                //END OPTIMIZATION or OCCUPANCY --------------------------------------------------------------
                } // end giant if to determine if Optimized or occupancy

                else{ // Not optimized and not occupancy

                // Adaptive Setpoint Control: _____________________________________________________________
                    // Not really needed, could be replaced by occupancyAdaptSetpoints.py
                    if (adaptiveSet[i]){
                        // Sets to a the minimum of 18.9 when outdoor temp outTemps < 10C, and max 30.2C when outTemps >= 33.5
                        // Uses sliding scale for 10 < outTemps < 33.5 C
                        // Note if temperature is consistently above 33.5C or below 10C no changes in setpoint will occur.
                        System.out.println("Adaptive Setpoint - using Outdoor Temp outTemps = " + outTemps[i]);
                        if (outTemps[i]<=10){
                            setHeat[i]=18.9;
                            setCool[i]=22.9;
                        }else if (outTemps[i]>=33.5){
                            setHeat[i]=26.2;
                            setCool[i]=30.2;
                        }else {
                            setHeat[i] = 0.31*outTemps[i] + 17.8-2+0.5;
                            setCool[i] = 0.31*outTemps[i] + 17.8+2+0.5;
                        }

                        if(hcc[i] == 'h'){
                            setCool[i]= 30.2;     // 23.0
                        }
                        else{
                            setHeat[i] = 15.0;   // 20.0 
                        }
                    } 
                // End Adaptive Setpoint Control -------------------------------------------------------

                // FIXED SETPOINT _________________________________________________________________________
                    else{ //Not adaptive, so fixed
                        if(hcc[i] == 'h'){
                            setCool[i] = 32.0;
                            setHeat[i] = 20.0;
                        }
                        else{ //cool
                            setCool[i]= 23.0;
                            setHeat[i] = 15.0;
                        }
                    }
                //END FIXED SETPT -------------------------------------------------------------------


                        //FUZZY CONTROL FOR NO OPTIMIZATION ______________________________________________________
                        // Does not activate IF USING OPTIMIZATION
                    double OFFSET = 0.5; // need to change slightly higher/lower so E+ doesnt have issues
                    if (hcc[i] == 'c'){
                        // For Cooling 1 degree under Cooling setpoint:
                        if (zoneTemps[i] >= setCool[i] - 0.2){ // check if likely to exceed maximum band. Was -0.1 but want to be more aggressive
                            fuzzy_cool = -1;
                        } else if (zoneTemps[i] <= setCool[i]-1.1){
                            fuzzy_cool = 1;
                        }
                        setCool[i] = setCool[i] - 0.6 +fuzzy_cool*OFFSET;   // - 0.6 so that oscillates 0.1-1.1 degree under cooling setpoint
                        setHeat[i] = 15.0; // Override for now to avoid turning on AC. 
                        }
                    else{ //Heat
                        // For Heating 1 degree under Heating setpoint:
                        if (zoneTemps[i] <= setHeat[i] + 0.2){ // check if likely to exceed minimum band
                            fuzzy_heat = 1;
                        } else if (zoneTemps[i] >= setHeat[i]+1.1){
                            fuzzy_heat = -1;
                        }
                        setHeat[i] = setHeat[i] + 0.6 +fuzzy_heat*OFFSET;  // + 0.6 so that oscillates 0.1-1.1 degree above heating setpoint
                        setCool[i] = 32.0; // Override for now to avoid turning on heat. 
                    }
                    
                    // END FUZZY NO OPT ------------------------------------------------------------
                } // end of giant else to indicate not optimized
                
                // End Setpoints =============================================================================
    
    //BEGIN APPLIANCE SCHEDULER ============================================================
                
                //System.out.println("TIMESTEP FOR APPLIANCE: " + (int)currentTime);
                System.out.println("OCCUPANCY AT TIMESTEP: " + occupancyData.get((int)currentTime));
                
                //clearing information from past day and getting new probability of activation
                //clearing past activations and finding new activation prob if it is a new day
                if (currentTime == 0) {
                    System.out.println("DAY COUNTER: " + dayCount);
                    activationProb = dailyActivationProb/timeStepsOccupied.get(dayCount-1);
                    System.out.println(activationProb);
                }else if (currentTime+1 == occupancyData.size()){
                    System.out.println("END OF SIMULATION");
                }else if ((currentTime+1)%(24*nt) == 0) {
                    numActToday = 0;
                    //dayCount = dayCount + 1;
                    // Problem: original version looped once for single sim
                    // multisim loops once per simulation, so for 2 simulations, daycount will increment twice at 
                    // each new day. 
                    // Replace with formula instead - Brian
                    // currentTime represents 5 mins elapsed
                    // nt = 12 constant
                    dayCount = (int)(currentTime+1)/(24*nt);
                    // should be int anyway, but forcing int to be safe
                    
                    sleepTime = sleepTime + nt*24;
                    wakeTime = wakeTime + nt*24;
                    System.out.println("DAY COUNTER: "+ dayCount); // failed after dayCount = 9
                    activationProb = dailyActivationProb/timeStepsOccupied.get(dayCount-1);
                    System.out.println(activationProb);
                }	
                //make sure activation history takes precedence
                if (activationHistory.size() > 0 && activationHistory.size() < runTime){
                    state = 1;
                    activationHistory.add(state);
                    stateHistory.add(state);
                    randomNumHistory.add(0.0);
                }else {
                    //dealing with occupancy
                    if (occupancyData.get((int)currentTime) == 1) {
                        //dealing with wake/sleep time
                        if (currentTime > wakeTime && currentTime < sleepTime) {
                            //dealing with number of activations per day
                            if (numActToday < numActPerDay) {
                                //dealing with length of operation
                                if (activationHistory.size() == runTime) {
                                    state = 0;
                                    stateHistory.add(state);
                                    numActToday = numActToday + 1;
                                    activationHistory.clear();
                                    randomNumHistory.add(0.0);
                                }else if (activationHistory.size() == 0) {
                                    double randomNum = Math.random(); //random num for monte carlo or add whatever determiner I decide
                                    randomNumHistory.add(randomNum);
                                    if (randomNum < activationProb) {
                                        state = 1;
                                        activationHistory.add(state);
                                        stateHistory.add(state);
                                    }else {
                                        state = 0;
                                        stateHistory.add(state); // end determiners
                                    }
                                }
                            }else {
                            state = 0;
                            stateHistory.add(state);
                            randomNumHistory.add(0.0);
                            }
                        }else {
                            state = 0;
                            stateHistory.add(state);
                            randomNumHistory.add(0.0);
                        }
                    }else {
                        state = 0;
                        stateHistory.add(state);
                        randomNumHistory.add(0.0);
                    }
                }
                //System.out.println("STATE HISTORY:"); //These never get reset so by end of simulation there may be thousand or more elements!
                //System.out.println(stateHistory);
                //System.out.println("RANDOM NUMBERS:");
                //System.out.println(randomNumHistory);

                //END APPLIANCE SCHEDULER =================================================================
                
                
                // DISPLAY WHAT GETS SENT, regardless of operating mode --------------------------------------ADD VARIABLES IF NEEDED
                System.out.println("setHeat[" + i + "] = "+setHeat[i] );
                System.out.println("setCool[" + i + "] = "+setCool[i] );
                System.out.println("Dishwasher Activation[" + i + "] = "+ state);
            
            
            }
            
            //+++++++++++++++++++++++++++++ End loop to calculate setpoints for each EnergyPlus simulation +++++++++++++++++++++++++++++//
            
            p=p+1; //timestep
			System.out.println("timestep p = "+String.valueOf(p)); //moved here so it runs once per loop of all sims
            
    // SEND VALUES to each socket federate -------------------------------------------------------ADD VARIABLES IF NEEDED
    //System.out.println("send to sockets interactions loop");
    for(int i=0;i<numSims;i++){
        // simID = i;  I am leaving this here to remind myself that i is simID for each socket
            
        dataStrings[i] = "epGetStartCooling"+varNameSeparator;
        dataStrings[i] = dataStrings[i] + String.valueOf(setCool[i]) + doubleSeparator;
        
        dataStrings[i] = dataStrings[i] + "epGetStartHeating"+varNameSeparator;
        dataStrings[i] = dataStrings[i] + String.valueOf(setHeat[i]) + doubleSeparator;

        dataStrings[i] = dataStrings[i] + "dishwasherSchedule"+varNameSeparator;
        dataStrings[i] = dataStrings[i] + String.valueOf(state) + doubleSeparator;

        //print out result
        System.out.println("Sending: dataStrings[" + i + "] = "+ dataStrings[i] );

            // SendModel vSendModel = create_SendModel();
            // vSendModel.set_dataString(dataString);
            // log.info("Sent sendModel interaction with {}", dataString);
            // vSendModel.sendInteraction(getLRC());

            Controller_Socket sendControls = create_Controller_Socket();
            sendControls.set_dataString(dataStrings[i]);
            sendControls.set_simID(i);
            //System.out.println("Send sendControls interaction: " + setCool[i] + " to Simulation #" + i);
            sendControls.sendInteraction(getLRC());

            dataStrings[i] = "";
	}

	System.out.println("timestep after sending Socket... should advance after this: " + currentTime);
	// End Send to Socket -----------------------------------------------------------

			if (writeFile){
				// Writing data to file ______________________________________________________
				try{
					// Create new file
					// New file naming method that goes to Deployment folder and has the time and date string - Brian
					// DataEP_YYYY-MM-DD_HH-mm.txt
					// TODO: Make something like this work (generate outputs for each simulation): File file = new File("DataEP_"+mode[i]+"_"+heatOrCool[i]+"_"+datime+".txt");
					File file = new File("DataEP_"+mode[0]+"_"+heatOrCool[0]+"_"+datime+".txt");

					// If file doesn't exists, then create it
					if (!file.exists()) {
						file.createNewFile();
						// Write header row
						FileWriter fw = new FileWriter(file.getAbsoluteFile(),true);
						BufferedWriter bw = new BufferedWriter(fw);
						bw.write("CurrentTime\tHour\tzoneTemps[°C]\toutTemps[°C]\tsolarRadiation[W/m^2]\treceivedHeatTemp[°C]\treceivedCoolTemp[°C]\tsetHeat[°C]\tsetCool[°C]\n");
						bw.close();
					}

					FileWriter fw = new FileWriter(file.getAbsoluteFile(),true);
					BufferedWriter bw = new BufferedWriter(fw);

					// Write in file

					for(int i=0;i<numSims;i++){
						// Not sure why but the hour variable was causing an unsuccessful build here
						// bw.write(currentTime+"\t"+hour+"\t"+ zoneTemps[i]+"\t"+ outTemps[i]+"\t"+ solarRadiation[i]+"\t" + receivedHeatTemp[i]+"\t"+ receivedCoolTemp[i]+"\t"+setHeat[i]+"\t"+setCool[i]+"\n");
						bw.write(currentTime+"\t"+ zoneTemps[i]+"\t"+ outTemps[i]+"\t"+ solarRadiation[i]+"\t" + receivedHeatTemp[i]+"\t"+ receivedCoolTemp[i]+"\t"+setHeat[i]+"\t"+setCool[i]+"\n");
					}

					// Close connection & save file
					bw.close();
				} catch(Exception e){
					System.out.println(e);
					System.out.println("Text Alert: Error when writing DataEP.txt file.");
				} // End File Write -------------------------------------------------------------
			}

            ////////////////////////////////////////////////////////////////////
            // TODO break here if ready to resign and break out of while loop //
            ////////////////////////////////////////////////////////////////////

            if (!exitCondition) {
                currentTime += super.getStepSize();
                AdvanceTimeRequest newATR =
                    new AdvanceTimeRequest(currentTime);
                putAdvanceTimeRequest(newATR);
                atr.requestSyncEnd();
                atr = newATR;
            }
        }

        // call exitGracefully to shut down federate
        exitGracefully();

        //////////////////////////////////////////////////////////////////////
        // TODO Perform whatever cleanups are needed before exiting the app //
        //////////////////////////////////////////////////////////////////////
    }

// This method is what controller does with the data sent from Socket.java
    private void handleInteractionClass(Socket_Controller interaction) {
        ///////////////////////////////////////////////////////////////
        // TODO implement how to handle reception of the interaction //
        ///////////////////////////////////////////////////////////////

        // can now exit while loop waiting for this interaction
        System.out.println("received Socket_Controller interaction");
        
        //receivedSocket variable was here
        
        // Supersocket sends 2 interactions, one per EP

        // Could make global var that holds simIDs but it would just be 0,1,2,...
        // int simID = 0;
        //int simID = interaction.get_simID();  //brian removed as test
        //try doing manually - less safe in case extra simID's appear but might get past whatever bug we have rn.
        //for (int simID = 0; simID <numSims; simID++){
			int simID = receivedSimsCount; // temporary fix
			
			System.out.println("numVars[" + simID + "] = " + numVars[simID]);
			holder[simID] = interaction.get_dataString();
			System.out.println("holder[" + simID + "] = "+ holder[simID] );

			System.out.println("handle interaction loop");
			
			//Fails somewhere in this loop
			String vars[] = holder[simID].split(doubleSeparator);
			//System.out.println("vars[0] = "+vars[0]);
			System.out.println("length of vars = " + vars.length); //vars.length = 20
			int j=0;
			for( String token : vars){
				//System.out.println("token = " +token);
				String token1[] = token.split(varNameSeparator);
				//System.out.println("token1[0] = "+token1[0]);
				//System.out.println("token1[1] = "+token1[1]); //after here
				// tried increasing length of varNames in initialization of array from 15 to 22 since
				// output said there are 20 items in vars and at end it says 
				// java.lang.ArrayIndexOutOfBoundsException: Index 15 out of bounds for length 15
				varNames[j] = token1[0];
				System.out.println("varNames[" + j + "] = "+ varNames[j] ); //reorder to help debugging 
				// figure out whether varNames or doubles is failing
				doubles[j] = token1[1];
				System.out.println("doubles[" + j + "] = "+ doubles[j] );
				j = j+1;
			}
			// x

			// organize varNames and doubles into vectors of values
			for(int i=0; i<j;i++){
			  if(varNames[i].equals("epSendZoneMeanAirTemp")){
				zoneTemps[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendOutdoorAirTemp")){
				outTemps[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendZoneHumidity")){
				zoneRHs[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendHeatingEnergy")){
				heatingEnergy[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendCoolingEnergy")){
				coolingEnergy[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendNetEnergy")){
				netEnergy[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendEnergyPurchased")){
				energyPurchased[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendEnergySurplus")){
				energySurplus[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendDayOfWeek")){
				dayOfWeek[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendSolarRadiation")){
				solarRadiation[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendHeatingSetpoint")){
				receivedHeatTemp[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("epSendCoolingSetpoint")){
				receivedCoolTemp[simID] = Double.valueOf(doubles[i]);
			  }
			  else if(varNames[i].equals("price")){
				price = Double.valueOf(doubles[i]);
			  }
			  // checking timesteps:
			  else if(varNames[i].equals("timestep")){
				timestep_Socket = doubles[i];
			  }
			  else {
				  System.out.println("Warning: Unrecognized varNames[ " + i + " ], \" " + varNames[i] + " \" = " +doubles[i] + "\n");
			  }
			}
		//} // end of for loop simID
		
		// SUPERSOCKET sends 1 interaction per EP sim
        //Requires waiting for all expected interactions from socket
        // this triggers on each interaction sent
        //  moved to avoid crashing everything
        receivedSimsCount += 1; 
        if (receivedSimsCount >= numSims){ receivedSocket = true; }
		
    }

// Automatically created method; don't change without a good reason
    public static void main(String[] args) {
        try {
            FederateConfigParser federateConfigParser =
                new FederateConfigParser();
            FederateConfig federateConfig =
                federateConfigParser.parseArgs(args, FederateConfig.class);
            Controller federate =
                new Controller(federateConfig);
            federate.execute();
            log.info("Federate Execution Completed Successfully!");
            System.exit(0);
        }
        catch (Exception e) {
            log.error(e);
            System.exit(1);
        }
    }
}
