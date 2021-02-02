'''
Created on Feb. 9, 2020

@author: cefect

simple build routines
'''

#==========================================================================
# logger setup-----------------------
#==========================================================================
import logging, configparser, datetime, shutil



#==============================================================================
# imports------------
#==============================================================================
import os
import numpy as np
import pandas as pd
from pandas import IndexSlice as idx

#Qgis imports

import processing
#==============================================================================
# custom imports
#==============================================================================
from hlpr.exceptions import QError as Error
    

from build.dikes.dcoms import Dcoms
    
#from hlpr.basic import get_valid_filename

#==============================================================================
# functions-------------------
#==============================================================================
class Dvuln(Dcoms):


    def __init__(self,
                 
                  *args,  **kwargs):
        
        super().__init__(*args,**kwargs)

        
        self.logger.debug('Dvuln.__init__ w/ feedback \'%s\''%type(self.feedback).__name__)
        
        
    def load_fcurves(self):
        pass
    

    

    

            
        