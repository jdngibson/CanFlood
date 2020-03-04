# -*- coding: utf-8 -*-
"""
ui class for the BUILD toolset
"""
#==============================================================================
# imports
#==============================================================================
import sys, os, warnings, tempfile, logging, configparser, datetime
import os.path
from shutil import copyfile

#qgis
from PyQt5 import uic
from PyQt5 import QtWidgets

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, QObject 
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QListWidget, QTableWidgetItem

from qgis.core import *
from qgis.analysis import *
import qgis.utils
import processing
from processing.core.Processing import Processing


#paths
"""not sure what these are doing"""
#sys.path.append(r'C:\IBI\_QGIS_\QGIS 3.8\apps\Python37\Lib\site-packages')
#sys.path.append(os.path.join(sys.exec_prefix, 'Lib/site-packages'))

file_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(file_dir)


"""
TODO: dependency check

"""
#==============================================================================
# pandas depdendency check
#==============================================================================
"""moved up
msg = 'requires pandas version >=0.25.3'
try:
    import pandas as pd
except:
    qgis.utils.iface.messageBar().pushMessage('CanFlood', msg, level=Qgis.Critical)
    raise ImportError(msg)
    
if not pd.__version__ >= '0.25.3':
    qgis.utils.iface.messageBar().pushMessage('CanFlood', msg, level=Qgis.Critical)
    raise ImportError(msg)"""

import pandas as pd
import numpy as np #Im assuming if pandas is fine, numpy will be fine


#==============================================================================
# custom imports
#==============================================================================

#import canflood_inprep.prep.wsamp
from prep.wsamp import WSLSampler
from prep.lisamp import LikeSampler
#from canFlood_model import CanFlood_Model
import hp
import hlpr.plug

from hlpr.basic import *

# This loads your .ui file so that PyQt can populate your plugin with the elements from Qt Designer
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'ProjectDataPrepDialog_Base.ui'))


class DataPrep_Dialog(QtWidgets.QDialog, FORM_CLASS, hlpr.plug.QprojPlug):
    
    event_name_set = [] #event names
    
    invalid_cids = ['fid', 'ogc_fid']
    
    def __init__(self, iface, parent=None):
        """these will only ini tthe first baseclass (QtWidgets.QDialog)"""
        super(DataPrep_Dialog, self).__init__(parent)
        super(DataPrep_Dialog, self).__init__(parent)
        self.setupUi(self)
        
        # Set up the user interface from Designer through FORM_CLASS.
        # After self.setupUi() you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://qt-project.org/doc/qt-4.8/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect

        self.ras = []
        self.ras_dict = {}
        self.vec = None

        self.iface = iface
        
        self.qproj_setup()
        
        self.connect_slots()
        

    def connect_slots(self):
        
        #self.testit()
        #======================================================================
        # pull project data
        #======================================================================
        #pull layer info from project
        rlays_d = dict()
        vlays_d = dict()
        for layname, layer in QgsProject.instance().mapLayers().items():
            if isinstance(layer, QgsVectorLayer):
                vlays_d[layname] = layer
            elif isinstance(layer, QgsRasterLayer):
                rlays_d[layname] = layer
            else:
                self.logger.debug('%s not filtered'%layname)
        #======================================================================
        # scenario setup tab----------
        #======================================================================
        #populate guis
        self.comboBox_vec.setFilters(QgsMapLayerProxyModel.VectorLayer) #SS. Inventory Layer: Drop down
        self.comboBox_aoi.setFilters(QgsMapLayerProxyModel.VectorLayer) #SS. Project AOI
        self.comboBox_SSelv.addItems(['datum', 'ground']) #ss elevation type
               
        
        #Working Directory
        def browse_wd():
            return self.browse_button(self.lineEdit_wd, prompt='Select Working Directory',
                                      qfd = QFileDialog.getExistingDirectory)
            
        self.pushButton_wd.clicked.connect(browse_wd) # SS. Working Dir. Browse
        
        #======================================================================
        # #Inventory Vector Layer
        #======================================================================
        def upd_cid():
            return self.mfcb_connect(
                self.mFieldComboBox_cid, self.comboBox_vec.currentLayer(),
                fn_str = 'id' )
                
        self.comboBox_vec.layerChanged.connect(upd_cid) #SS inventory vector layer
        
        #find a good layer
        try:
            for layname, vlay in vlays_d.items():
                if layname.startswith('finv'):
                    break
            
            self.logger.info('setting comboBox_vec = %s'%vlay.name())
            self.comboBox_vec.setLayer(vlay)
        except Exception as e:
            self.logger.warning('failed to set inventory layer w: \n    %s'%e)
        
        #Vulnerability Curve Set
        def browse_curves():
            return self.browse_button(self.lineEdit_curve, prompt='Select Curve Set',
                                      qfd = QFileDialog.getOpenFileName)
            
        self.pushButton_SScurves.clicked.connect(browse_curves)# SS. Vuln Curve Set. Browse
        
        #program controls
        self.checkBox_SSoverwrite.stateChanged.connect(self.set_overwrite) #SS overwrite data files
        
        #generate new control file      
        self.pushButton_generate.clicked.connect(self.build_scenario) #SS. generate
        
        #CanFlood Control File
        self.pushButton_cf.clicked.connect(self.browse_cf)# SS. Model Control File. Browse
        
        #======================================================================
        # hazard sampler---------
        #======================================================================
        # Set GUI elements
        self.comboBox_ras.setFilters(QgsMapLayerProxyModel.RasterLayer)
        """
        todo: swap this out with better selection widget
        """
        #selection       
        self.pushButton_remove.clicked.connect(self.remove_text_edit)
        self.pushButton_clear.clicked.connect(self.clear_text_edit)
        self.pushButton_add_all.clicked.connect(self.add_all_text_edit)
        
        self.comboBox_ras.currentTextChanged.connect(self.add_ras)
        
        #execute
        self.pushButton_HSgenerate.clicked.connect(self.run_wsamp)
        
        #======================================================================
        # event likelihoods
        #======================================================================
        self.pushButton_ELstore.clicked.connect(self.store_eaep)
        
        """dev button
        self.pushButton_ELdev.clicked.connect(self._pop_el_table)"""
        
        
        #======================================================================
        # Likelihood Sampler-----------
        #======================================================================
        """todo: rename the buttons so they align w/ the set labels"""
        #list of combo box names on the likelihood sampler tab
        self.ls_cb_d = { #set {hazard raster : lpol}
            1: (self.MLCB_LS1_event_3, self.MLCB_LS1_lpol_3),
            2: (self.MLCB_LS1_event_4, self.MLCB_LS1_lpol_4),
            3: (self.MLCB_LS1_event_5, self.MLCB_LS1_lpol_5),
            4: (self.MLCB_LS1_event, self.MLCB_LS1_lpol),
            5: (self.MLCB_LS1_event_6, self.MLCB_LS1_lpol_6),
            6: (self.MLCB_LS1_event_7, self.MLCB_LS1_lpol_7),
            7: (self.MLCB_LS1_event_2, self.MLCB_LS1_lpol_2),
            8: (self.MLCB_LS1_event_8, self.MLCB_LS1_lpol_8)
            }
        
        #loop and set filteres
        first = True
        for sname, (mlcb_haz, mlcb_lpol) in self.ls_cb_d.items():
            #set drop down filters
            mlcb_haz.setFilters(QgsMapLayerProxyModel.RasterLayer)
            mlcb_haz.setAllowEmptyLayer(True)
            mlcb_lpol.setFilters(QgsMapLayerProxyModel.PolygonLayer)
            mlcb_lpol.setAllowEmptyLayer(True)
            
            if first:
                mlcb_lpol_1 = mlcb_lpol
                first = False

            
        #connect to update the field name box
        def upd_lfield(): #updating the field box
            return self.mfcb_connect(
                self.mFieldComboBox_LSfn, mlcb_lpol_1.currentLayer(),
                fn_str = 'fail' )
    
        
        mlcb_lpol_1.layerChanged.connect(upd_lfield)
        
            
        #connect execute
        self.pushButton_LSsample.clicked.connect(self.run_lisamp)
                    
        #======================================================================
        # DTM sampler
        #======================================================================
        self.comboBox_dtm.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.pushButton_DTMsamp.clicked.connect(self.run_dsamp)
        
        #======================================================================
        # validator
        #======================================================================
        self.pushButton_Validate.clicked.connect(self.run_validate)
        

        #======================================================================
        # general
        #======================================================================
        self.buttonBox.accepted.connect(self.reject)
        self.buttonBox.rejected.connect(self.reject)
        self.pushButton_help.clicked.connect(self.run_help)
        self.logger.info('DataPrep ui initilized')
        #======================================================================
        # dev
        #======================================================================
        """"
        to speed up testing.. manually configure the project
        """
        
        self.lineEdit_cf_fp.setText(r'C:\LS\03_TOOLS\CanFlood\_wdirs\20200304\CanFlood_scenario1.txt')
        self.lineEdit_wd.setText(r'C:\LS\03_TOOLS\CanFlood\_wdirs\20200304')
        
        
        
    
    #==========================================================================
    # UI Buttom Actions-----------------      
    #==========================================================================
              
    def xxxbrowse_curves(self): #SS. Vulnerability Curve Set. file path
        """todo: set filter to xls only"""
        filename = QFileDialog.getOpenFileName(self, "Select Vulnerability Curve Set")[0] 
        if not filename == '':
            self.lineEdit_curve.setText(filename) #display the user selected filepath
             
            self.logger.push('curve set selected')
            self.logger.info(filename)

    def browse_cf(self): #select an existing model control file
        self.browse_button(self.lineEdit_cf_fp, prompt='Select CanFlood control file',
                           qfd=QFileDialog.getOpenFileName)
        
        """
        TODO: Populate Vulnerability Curve Set box
         
        Check the control file is the correct format
         
        print out all the values pressent in the control file
        """

    def xxxbrowse_wd(self):
        #laungh the gui
        wd = QFileDialog.getExistingDirectory(self, "Select Working Directory")
        
        #fill in the text and check it
        if wd not in "": #see if it was cancelled
            self.lineEdit_wd.setText(os.path.normpath(wd))
            
            if not os.path.exists(wd):
                os.makedirs(wd)
                self.logger.info('requested working directory does not exist. built')
            
            self.logger.push('set working directory')
            
        
            

        
    def xxxupdate_cid_cb(self): #update teh fields drop down any time the main layer changes
        
        try:
            #self.logger.info('user changed finv layer to %s'%self.comboBox_vec.currentLayer().name())
            self.mFieldComboBox_cid.setLayer(self.comboBox_vec.currentLayer()) #field selector
            
            #try and find a good match
            for field in self.comboBox_vec.currentLayer().fields():
                if 'id' in field.name():
                    self.logger.debug('matched on field %s'%field.name())
                    break
                
            self.mFieldComboBox_cid.setField(field.name())
            
        except Exception as e:
            self.logger.info('failed set current layer w/ \n    %s'%e)
        


        
        
    #==========================================================================
    # Layer Loading---------------
    #==========================================================================
    def add_ras(self):
        x = [str(self.listWidget_ras.item(i).text()) for i in range(self.listWidget_ras.count())]
        self.ras_dict.update({ (self.comboBox_ras.currentText()) : (self.comboBox_ras.currentLayer()) })
        if (self.comboBox_ras.currentText()) not in x:
            self.listWidget_ras.addItem(self.comboBox_ras.currentText())
            self.ras_dict.update({ (self.comboBox_ras.currentText()) : (self.comboBox_ras.currentLayer()) })
        
    def clear_text_edit(self):
        if len(self.ras_dict) > 0:
            self.listWidget_ras.clear()
            self.ras_dict = {}
    
    def remove_text_edit(self):
        if (self.listWidget_ras.currentItem()) is not None:
            value = self.listWidget_ras.currentItem().text()
            item = self.listWidget_ras.takeItem(self.listWidget_ras.currentRow())
            item = None
            for k in list(self.ras_dict):
                if k == value:
                    self.ras_dict.pop(value)

    def add_all_text_edit(self):
        layers = self.iface.mapCanvas().layers()
        #layers_vec = [layer for layer in layers if layer.type() == QgsMapLayer.VectorLayer]
        layers_ras = [layer for layer in layers if layer.type() == QgsMapLayer.RasterLayer]
        x = [str(self.listWidget_ras.item(i).text()) for i in range(self.listWidget_ras.count())]
        for layer in layers_ras:
            if (layer.name()) not in x:
                self.ras_dict.update( { layer.name() : layer} )
                self.listWidget_ras.addItem(str(layer.name()))

    #==========================================================================
    # tool commands------------                   
    #==========================================================================
    def run_help(self):
        """todo: link to help pdf"""
        raise Error('not implemented')
    
    
    def slice_aoi(self, vlay):
        
        aoi_vlay = self.comboBox_aoi.currentLayer()
        
        if aoi_vlay is None:
            self.logger.info('no aoi selected... not slicing')
            return vlay
        else:
            self.logger.warning('aoi slicing not impelemented')
            return vlay
            
            #raise Error('aoi slicing not implemented')
        
        
    
    def build_scenario(self): #called by Scenario Setup 'Build'
        
        self.tag = self.linEdit_ScenTag.text() #set the secnario tag from user provided name
        
        self.cid = self.mFieldComboBox_cid.currentField() #user selected field
        
        self.wd =  self.lineEdit_wd.text() #pull the wd filepath from the user provided in 'Browse'
        
        finv_fp = self.convert_finv() #convert the finv to csv and write to file
        
        #======================================================================
        # build the control file
        #======================================================================
        #called by build_scenario()
        dirname = os.path.dirname(os.path.abspath(__file__))
        
        #get the default template from the program files
        cf_src = os.path.join(dirname, '_documents/CanFlood_control_01.txt')
        #cf_src = os.path.join(dirname, '_documents/CanFlood_control_01.txt')
        
        #start the scratch file
        scratch_src = os.path.join(dirname, '_documents/scratch.txt')
        
        #get control file name from user provided tag
        cf_fn = 'CanFlood_%s.txt'%self.tag
        cf_path = os.path.join(self.wd, cf_fn)
        #cf_path = os.path.join(self.wd, 'CanFlood_control_01.txt')
        
        #see if this exists
        if os.path.exists(cf_path):
            msg = 'generated control file already exists. overwrite=%s \n     %s'%(
                self.overwrite, cf_path)
            if self.overwrite:
                self.logger.warning(msg)
            else:
                raise Error(msg)
            
        
        #copy over the default template
        copyfile(cf_src, cf_path)
            
        if not os.path.exists(scratch_src):
            open(scratch_src, 'w').close()
        
        #======================================================================
        # update the control file
        #======================================================================
        pars = configparser.ConfigParser(allow_no_value=True)
        _ = pars.read(cf_path) #read it from the new location
        
        #parameters
        pars.set('parameters', 'cid', self.cid) #user selected field
        pars.set('parameters', 'name', self.tag) #user selected field
        pars.set('parameters', 'felv', self.comboBox_SSelv.currentText()) #user selected field
        
        #filepaths
        pars.set('dmg_fps', 'curves',  self.lineEdit_curve.text())
        pars.set('dmg_fps', 'finv', finv_fp)
        
        """shoul donly be set by corresponding tools
        pars.set('dmg_fps', 'expos', os.path.normpath(os.path.join(self.wd, 'expos_test_1_7.csv')))
        pars.set('dmg_fps', '#expos file path set from wsamp.py')
        pars.set('dmg_fps', 'gels', os.path.normpath(os.path.join(self.wd, 'gel_cT1.csv')))"""
        
        """should only be set by the Impact model
        pars.set('risk_fps', 'dmgs', os.path.normpath(os.path.join(self.wd, 'dmg_results.csv')))
        pars.set('risk_fps', 'exlikes', os.path.normpath(os.path.join(self.wd, 'elikes_cT1.csv')))
        pars.set('risk_fps', 'aeps', os.path.normpath(os.path.join(self.wd, 'eaep_cT1.csv')))"""
        
        #set note
        pars.set('parameters', '#control file template created from \'scenario setup\' on  %s'%(
            datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')
            ))
        
        #write the config file 
        with open(cf_path, 'w') as configfile:
            pars.write(configfile)
            
        QgsMessageLog.logMessage("default CanFlood model config file created :\n    %s"%cf_path,
                                 'CanFlood', level=Qgis.Info)
        
        """NO. should only populate this automatically from ModelControlFile.Browse
        self.lineEdit_curve.setText(os.path.normpath(os.path.join(self.wd, 'CanFlood - curve set 01.xls')))"""
        
        """TODO:
        write aoi filepath to scratch file
        """
        
        #======================================================================
        # wrap
        #======================================================================
        
        #display the control file in the dialog
        self.lineEdit_cf_fp.setText(cf_path)
        
        """not sure what this is
        self.lineEdit_control_2.setText(os.path.normpath(os.path.join(self.wd, 'CanFlood_control_01.txt')))"""
        
        self.logger.push("Scenario \'%s\' control file created"%self.tag)

        
    def convert_finv(self): #convert the finv vector to csv file
        
        #======================================================================
        # check the cid
        #======================================================================
        if self.cid == '' or self.cid in self.invalid_cids:
            raise Error('user selected invalid cid \'%s\''%self.cid)  
        
            
        
        #store the vecotr layer
        self.finv_vlay = self.comboBox_vec.currentLayer()
        
        #extract data
        df = hp.vlay_get_fdf(self.finv_vlay)
          
        #drop geometery indexes
        for gindx in self.invalid_cids:   
            df = df.drop(gindx, axis=1, errors='ignore')
            
        if not self.cid in df.columns:
            raise Error('cid not found in finv_df')
        
        #write it as a csv
        out_fp = os.path.join(self.wd, 'finv_%s_%s.csv'%(self.tag, self.finv_vlay.name()))
        df.to_csv(out_fp, index=False)  
        
        QgsMessageLog.logMessage("inventory csv written to file:\n    %s"%out_fp,
                                 'CanFlood', level=Qgis.Info)
        
        return out_fp
                
        

    
    def run_wsamp(self): #execute wsamp
        log = self.logger.getChild('run_wsamp')

        log.info('user pressed \'pushButton_HSgenerate\'')
        #=======================================================================
        # assemble/prepare inputs
        #=======================================================================
        finv_raw = self.comboBox_vec.currentLayer()
        rlay_l = list(self.ras_dict.values())
        
        crs = self.qproj.crs()

        cf_fp = self.get_cf_fp()
        out_dir = self.lineEdit_wd.text()
        

        #update some parameters
        cid = self.mFieldComboBox_cid.currentField() #user selected field
        
        #======================================================================
        # aoi slice
        #======================================================================
        finv = self.slice_aoi(finv_raw)
        

        #======================================================================
        # precheck
        #======================================================================

        if finv is None:
            raise Error('got nothing for finv')
        if not isinstance(finv, QgsVectorLayer):
            raise Error('did not get a vector layer for finv')
        
        for rlay in rlay_l:
            if not isinstance(rlay, QgsRasterLayer):
                raise Error('unexpected type on raster layer')
            
        if not os.path.exists(out_dir):
            raise Error('working directory does not exist:  %s'%out_dir)
        
        if cid is None or cid=='':
            raise Error('need to select a cid')
        
        if not cid in [field.name() for field in finv.fields()]:
            raise Error('requested cid field \'%s\' not found on the finv_raw'%cid)
            
        assert os.path.exists(cf_fp), 'bad control file specified'
        #======================================================================
        # execute
        #======================================================================
        """
        finv = self.wsampRun(rlay_l, finv, control_fp=cf_fp1, cid=cid, crs=crs)"""
        #build the sample
        wrkr = WSLSampler(logger=self.logger, 
                          tag = self.tag, #set by build_scenario() 
                          feedback = self.feedback, #needs to be connected to progress bar
                          )
        """
        wrkr.tag
        """
        
        res_vlay = wrkr.run(rlay_l, finv, cid=cid, crs=crs)
        
        #check it
        wrkr.check()
        
        #save csv results to file
        wrkr.write_res(res_vlay, out_dir = out_dir)
        
        #update ocntrol file
        wrkr.upd_cf(cf_fp)
        
        #======================================================================
        # post---------
        #======================================================================
        """
        the hazard sampler sets up a lot of the other tools
        """
        #======================================================================
        # add to map
        #======================================================================
        if self.checkBox_HSloadres.isChecked():
            self.qproj.addMapLayer(res_vlay)
            self.logger.info('added \'%s\' to canvas'%res_vlay.name())
            
        #======================================================================
        # update event names
        #======================================================================
        self.event_name_set = [lay.name() for lay in rlay_l]
        
        log.info('set %i event names: \n    %s'%(len(self.event_name_set), 
                                                         self.event_name_set))
        
        #======================================================================
        # populate Event Likelihoods table
        #======================================================================
        l = self.event_name_set
        for tbl in [self.fieldsTable_EL]:

            tbl.setRowCount(len(l)) #add this many rows
            
            for rindx, ename in enumerate(l):
                tbl.setItem(rindx, 0, QTableWidgetItem(ename))
            
        log.info('populated tables with event names')
        
        #======================================================================
        # populate lisamp
        #======================================================================
        
                #get the mlcb
                
        try:
            rlay_d = {indxr: rlay for indxr, rlay in enumerate(rlay_l)}
            
            for indxr, (sname, (mlcb_h, mlcb_v)) in enumerate(self.ls_cb_d.items()):
                if indxr in rlay_d:
                    mlcb_h.setLayer(rlay_l[indxr])
                    
                else:
                    """
                    todo: clear the remaining comboboxes
                    """
                    break


        except Exception as e:
            log.error('failed to populate lisamp fields w/\n    %s'%e)
            
        
        #======================================================================
        # wrap
        #======================================================================
        self.logger.push('wsamp finished')
        
        return
    
    def run_dsamp(self): #sample dtm raster
        
        self.logger.info('user pressed \'pushButton_DTMsamp\'')

        
        #=======================================================================
        # assemble/prepare inputs
        #=======================================================================
        
        finv_raw = self.comboBox_vec.currentLayer()
        rlay = self.comboBox_dtm.currentLayer()
        
        crs = self.qproj.crs()

        cf_fp = self.get_cf_fp()
        out_dir = self.lineEdit_wd.text()
        

        #update some parameters
        cid = self.mFieldComboBox_cid.currentField() #user selected field
        

        #======================================================================
        # aoi slice
        #======================================================================
        finv = self.slice_aoi(finv_raw)
        

        #======================================================================
        # precheck
        #======================================================================
                
        if finv is None:
            raise Error('got nothing for finv')
        if not isinstance(finv, QgsVectorLayer):
            raise Error('did not get a vector layer for finv')
        

        if not isinstance(rlay, QgsRasterLayer):
            raise Error('unexpected type on raster layer')
            
        if not os.path.exists(out_dir):
            raise Error('working directory does not exist:  %s'%out_dir)
        
        if cid is None or cid=='':
            raise Error('need to select a cid')
        
        if not cid in [field.name() for field in finv.fields()]:
            raise Error('requested cid field \'%s\' not found on the finv_raw'%cid)
            
        
        #======================================================================
        # execute
        #======================================================================

        #build the sample
        wrkr = WSLSampler(logger=self.logger, 
                          tag=self.tag, #set by build_scenario() 
                          feedback = self.feedback, #needs to be connected to progress bar
                          )
        
        res_vlay = wrkr.run([rlay], finv, cid=cid, crs=crs, fname='gels')
        
        #check it
        wrkr.check()
        
        #save csv results to file
        wrkr.write_res(res_vlay, out_dir = out_dir)
        
        #update ocntrol file

        
        #======================================================================
        # add to map
        #======================================================================
        if self.checkBox_DTMloadres.isChecked():
            self.qproj.addMapLayer(finv)
            self.logger.info('added \'%s\' to canvas'%finv.name())
            
        self.logger.push('dsamp finished')    
        
    def run_lisamp(self): #sample dtm raster
        
        self.logger.info('user pressed \'pushButton_DTMsamp\'')

        
        #=======================================================================
        # assemble/prepare inputs
        #=======================================================================
        finv_raw = self.comboBox_vec.currentLayer()
        crs = self.qproj.crs()
        cf_fp = self.get_cf_fp()
        out_dir = self.lineEdit_wd.text()
        cid = self.mFieldComboBox_cid.currentField() #user selected field
        
        lfield = self.mFieldComboBox_LSfn.currentField()
        
        #collect lpols
        lpol_d = dict()
        for sname, (mlcb_haz, mlcb_lpol) in self.ls_cb_d.items():
            hlay = mlcb_haz.currentLayer()
            
            if not isinstance(hlay, QgsRasterLayer):
                continue
            
            lpol_vlay = mlcb_lpol.currentLayer()
            
            if not isinstance(lpol_vlay, QgsVectorLayer):
                raise Error('must provide a matching VectorLayer for set %s'%sname)

            lpol_d[hlay.name()] = lpol_vlay 
            
        #======================================================================
        # aoi slice
        #======================================================================
        finv = self.slice_aoi(finv_raw)
        

        #======================================================================
        # precheck
        #======================================================================
                
        if finv is None:
            raise Error('got nothing for finv')
        if not isinstance(finv, QgsVectorLayer):
            raise Error('did not get a vector layer for finv')
                    
        if not os.path.exists(out_dir):
            raise Error('working directory does not exist:  %s'%out_dir)
        
        if cid is None or cid=='':
            raise Error('need to select a cid')
        
        if lfield is None or lfield=='':
            raise Error('must select a valid lfield')
        
        if not cid in [field.name() for field in finv.fields()]:
            raise Error('requested cid field \'%s\' not found on the finv_raw'%cid)
            
        
        
        #======================================================================
        # execute
        #======================================================================

        #build the sample
        wrkr = LikeSampler(logger=self.logger, 
                          tag=self.tag, #set by build_scenario() 
                          feedback = self.feedback, #needs to be connected to progress bar
                          crs = crs,
                          )
        
        res_df = wrkr.run(finv, lpol_d, cid=cid, lfield=lfield)
        
        #check it
        wrkr.check()
        
        #save csv results to file
        wrkr.write_res(res_df, out_dir = out_dir)
        
        #update ocntrol file
        wrkr.upd_cf(cf_fp)
        
        #======================================================================
        # add to map
        #======================================================================
        if self.checkBox_LSloadres.isChecked():
            res_vlay = wrkr.vectorize(res_df)
            self.qproj.addMapLayer(res_vlay)
            self.logger.info('added \'%s\' to canvas'%finv.name())
            
        self.logger.push('lisamp finished')    
        
        return
        
    def _pop_el_table(self): #developing the table widget
        

        l = ['e1', 'e2', 'e3']
        tbl = self.fieldsTable_EL
        tbl.setRowCount(len(l)) #add this many rows
        
        for rindx, ename in enumerate(l):
            tbl.setItem(rindx, 0, QTableWidgetItem(ename))
            
        self.logger.push('populated likelihoods table with event names')
            
            
    
    def store_eaep(self): #saving the event likelihoods table to file
        log = self.logger.getChild('store_eaep')
        log.info('user pushed \'pushButton_ELstore\'')
        

        #======================================================================
        # collect variables
        #======================================================================
        #get displayed control file path
        cf_fp = self.get_cf_fp()
        out_dir = self.lineEdit_wd.text()
        
        #likelihood paramter
        if self.radioButton_ELari.isChecked():
            event_probs = 'ari'
        else:
            event_probs = 'aep'
        self.logger.info('\'event_probs\' set to \'%s\''%event_probs)
        
        
        #======================================================================
        # collcet table data
        #======================================================================

        df = hp.qtbl_get_df(self.fieldsTable_EL)
        
        self.logger.info('extracted data w/ %s \n%s'%(str(df.shape), df))
        
        # check it
        if df.iloc[:, 1].isna().any():
            raise Error('got %i nulls in the likelihood column'%df.iloc[:,1].isna().sum())
        
        miss_l = set(self.event_name_set).symmetric_difference(df.iloc[:,0].values)
        if len(miss_l)>0:
            raise Error('event name mismatch')
        
        
        #======================================================================
        # clean it
        #======================================================================
        aep_df = df.set_index(df.columns[0]).iloc[:,0].to_frame().T
        

        
        #======================================================================
        # #write to file
        #======================================================================
        ofn = os.path.join(self.lineEdit_wd.text(), 'aeps_%i_%s.csv'%(len(aep_df.columns), self.tag))
        
        from hlpr.Q import Qcoms
        #build a shell worker for these taxks
        wrkr = Qcoms(logger=log, tag=self.tag, feedback=self.feedback, out_dir=out_dir)
        
        eaep_fp = wrkr.output_df(aep_df, ofn, 
                                  overwrite=self.overwrite, write_index=False)
        
        
        
        #======================================================================
        # update the control file
        #======================================================================
        wrkr.update_cf(
            {
                'parameters':({'event_probs':event_probs},),
                'risk_fps':({'aeps':eaep_fp}, 
                            '#aeps file path set from wsamp.py at %s'%(
                                datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')))
                          
             },
            cf_fp = cf_fp
            )
        
        
            
        self.logger.push('generated \'aeps\' and set \'event_probs\' to control file')
        
    def run_validate(self):
        #raise Error('broken')
        """
        a lot of this is duplicated in  model.scripts_.setup_pars
        
        TODO: consolidate with setup_pars
        
        """
        log = self.logger.getChild('valid')
        log.info('user pressed \'pushButton_Validate\'')
        
        #======================================================================
        # load the control file
        #======================================================================
        #get the control file path
        cf_fp = self.get_cf_fp()
        
        #build/run theparser
        log.info('validating control file: \n    %s'%cf_fp)
        pars = configparser.ConfigParser(inline_comment_prefixes='#', allow_no_value=True)
        _ = pars.read(cf_fp) #read it
        
        #======================================================================
        # assemble the validation parameters
        #======================================================================
        #import the class objects
        from model.dmg2 import Dmg2
        from model.risk2 import Risk2
        from model.risk1 import Risk1
        
        #populate all possible test parameters
        """
        todo: finish this
        """
        vpars_pos_d = {
                    'risk1':(self.checkBox_Vr1, Risk1),
                   'dmg2':(self.checkBox_Vi2, Dmg2),
                   'risk2':(self.checkBox_Vr2, Risk2),
                   #'risk3':(self.checkBox_Vr3, (None, None, None)),
                                           }
        
        #select based on user check boxes
        vpars_d = dict()
        for vtag, (checkBox, model) in vpars_pos_d.items():
            if checkBox.isChecked():
                vpars_d[vtag] = model
                
        if len(vpars_d) == 0:
            raise Error('no validation options selected!')
        
        log.info('user selected %i validation parameter sets'%len(vpars_d))
        
        #======================================================================
        # validate
        #======================================================================

        
        vflag_d = dict()
        for vtag, model in vpars_d.items():
            

            """needto play with init sequences to get this to work"""
    #==========================================================================
    #         #==================================================================
    #         # check expectations
    #         #==================================================================
    #         for sect, vchk_d in model.exp_pars_md.items():
    #             
    #             #check attributes
    #             for varnm, achk_d in vchk_d.items():

    #                 assert hasattr(model, varnm), '\'%s\' does not exist on %s'%(varnm, model)
    # 
    #                 
    #                 #==============================================================
    #                 # #get value from parameter                
    #                 #==============================================================
    #                 pval_raw = pars[sect][varnm]
    #                 
    #                 #get native type
    #                 ntype = type(getattr(model, varnm))
    #                 
    #                 #special type retrivial
    #                 if ntype == bool:
    #                     pval = pars[sect].getboolean(varnm)
    #                 else:
    #                     #set the type
    #                     pval = ntype(pval_raw)
    #                 
    #                 #==============================================================
    #                 # check it
    #                 #==============================================================
    #                 model.par_hndl_chk(sect, varnm, pval, achk_d)
    #==========================================================================
                    
            #==================================================================
            # set validation flag
            #==================================================================
            vflag_d[model.valid_par] = 'True'
            
        #======================================================================
        # update control file
        #======================================================================
        self.update_cf(
            {'validation':(vflag_d, )
             },
            cf_fp = cf_fp
            )
        
        
        #======================================================================
        # validate parameters
        #======================================================================
    #==========================================================================
    #     dfiles_d = dict() #collective list of data files for secondary checking
    #     for vtag, (section, eprops, epars) in vpars_d.items():
    #         log.info('checking %s'%vtag)
    #         #==================================================================
    #         # #check variables
    #         #==================================================================
    #         if not epars is None:
    #             for sect_chk, vars_l in epars.items():
    #                 assert sect_chk in pars.sections(), 'missing expected section %s'%sect_chk
    #                 
    #                 for varnm in vars_l:
    #                     assert varnm in pars[sect_chk], 'missing expected variable \'%s.%s\''%(sect_chk, varnm)
    #         else:
    #             log.warning('\'%s\' has no variable validation parameters!'%vtag)
    #                 
    #                 
    #         #==================================================================
    #         # #check expected data files
    #         #==================================================================
    #         if not eprops is None:
    #             for varnm, dprops_d in eprops.items():
    #                 
    #                 #see if this is in the control file
    #                 assert varnm in pars[section], '\'%s\' expected \'%s.%s\''%(vtag, section, varnm)
    #                 
    #                 #get the filepath
    #                 fp = pars[section][varnm]
    #                 fh_clean, ext = os.path.splitext(os.path.split(fp)[1])
    # 
    #                 #check existance
    #                 if not os.path.exists(fp):
    #                     log.warning('specified \'%s\' filepath does not exist: \n    %s'%(varnm, fp))
    #                     continue
    #                 
    #                 #load the data
    #                 if ext == '.csv':
    #                     data = pd.read_csv(fp, header=0, index_col=None)
    #                 elif ext == '.xls':
    #                     data = pd.read_excel(fp)
    #                 else:
    #                     raise Error('unepxected filetype for \"%s.%s\' = \"%s\''%(vtag, varnm, ext))
    #                 
    #                 #add this to the collective
    #                 if not varnm in dfiles_d:
    #                     dfiles_d[varnm] = data
    #                 
    #                 
    #                 #check the data propoerites expectations
    #                 for chk_type, evals in dprops_d.items():
    #                     
    #                     #extension
    #                     if chk_type == 'ext':
    #                         assert ext in evals, '\'%s\' got unexpected extension: %s'%(varnm, ext)
    #                         
    #                     #column names
    #                     elif chk_type == 'colns':
    #                         miss_l = set(evals).difference(data.columns)
    #                         assert len(miss_l)==0, '\'%s\' is missing %i expected column names: %s'%(
    #                             varnm, len(miss_l), miss_l)
    #                         
    #                     else:
    #                         raise Error('unexpected chk_type: %s'%chk_type)
    #                     
    #                 log.info('%s.%s passed %i data expectation checks'%(
    #                     vtag, varnm, len(dprops_d)))
    #         else:
    #             log.warning('\'%s\' has no data property validation parameters!'%vtag)
    #                     
    #             
    #         #==================================================================
    #         # #set validation flag
    #         #==================================================================
    #         pars.set('validation', vtag, 'True')
    #         log.info('\'%s\' validated'%vtag)
    #         
    #     
    #     #======================================================================
    #     # secondary checks
    #     #======================================================================
    #     """for special data checks (that apply to multiple models)"""
    #     for dname, data in dfiles_d.items():
    #         pass
    #     
    #     #======================================================================
    #     # update control file
    #     #======================================================================
    #     with open(cf_fp, 'w') as configfile:
    #         pars.write(configfile)
    #         
    #     log.info('updated control file:\n    %s'%cf_fp)
    #         
    #         
    #     #======================================================================
    #     # wrap
    #     #======================================================================
    #     log.push('validated %i model parameter sets'%len(vpars_d))
    #     
    #     return
    #==========================================================================
            
            
            
                    
                
            
        

            
            
             
        
        
        
                
                
            
    #==========================================================================
    # def run(self):
    #     # Do something useful here - delete the line containing pass and
    #     # substitute with your code.
    #     #=======================================================================
    #     # calculate poly stats
    #     #=======================================================================
    #     self.vec = self.comboBox_vec.currentLayer()
    #     self.ras = list(self.ras_dict.values())
    #     self.cf = self.lineEdit_cf_fp.text()
    #     if (self.vec is None or len(self.ras) == 0 or self.wd is None or self.cf is None):
    #         self.iface.messageBar().pushMessage("Input field missing",
    #                                              level=Qgis.Critical, duration=10)
    #         return
    #     
    #     """moved to build_scenario
    #     pars = configparser.ConfigParser(inline_comment_prefixes='#', allow_no_value=True)
    #     _ = pars.read(self.cf)
    #     pars.set('dmg_fps', 'curves', self.lineEdit_curve.text())
    #     with open(self.cf, 'w') as configfile:
    #         pars.write(configfile)"""
    #     
    #     
    #     canflood_inprep.prep.wsamp.main_run(self.ras, self.vec, self.wd, self.cf)
    #     self.iface.messageBar().pushMessage(
    #         "Success", "Process successful", level=Qgis.Success, duration=10)
    #     
    #==========================================================================

 

           
            
                    
            