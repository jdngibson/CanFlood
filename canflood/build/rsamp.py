'''
Created on Feb. 9, 2020

@author: cefect
'''

#==========================================================================
# logger setup-----------------------
#==========================================================================
import logging, configparser, datetime
start = datetime.datetime.now()


#==============================================================================
# imports------------
#==============================================================================
import os
import numpy as np
import pandas as pd


#Qgis imports
from qgis.core import QgsVectorLayer, QgsRasterLayer, QgsFeatureRequest, QgsProject, \
    QgsWkbTypes, QgsProcessingFeedback, QgsCoordinateTransform
    
from qgis.analysis import QgsRasterCalculatorEntry, QgsRasterCalculator
import processing
#==============================================================================
# custom imports
#==============================================================================

from hlpr.exceptions import QError as Error
    


from hlpr.Q import Qcoms,vlay_get_fdf, vlay_get_fdata, view, vlay_rename_fields
from hlpr.plot import Plotr

#==============================================================================
# functions-------------------
#==============================================================================
class Rsamp(Plotr, Qcoms):
    """ sampling hazard rasters from the inventory
    
    METHODS:
        run(): main caller for Hazard Sampler 'Sample' button
    
    
    """
    out_fp = None
    names_d = None
    rname_l =None
    
    
    psmp_codes = {
                 0:'Count',
                 1: 'Sum',
                 2: 'Mean',
                 3: 'Median',
                 #4: Std. dev.
                 5: 'Min',
                 6: 'Max',
                # 7: Range
                # 8: Minority
                # 9: Majority (mode)
                # 10: Variety
                # 11: Variance
                # 12: All
                }
    

    
    dep_rlay_d = dict() #container for depth rasters (for looped runs)
    
    impactfmt_str = '.2f' #formatting impact values on plots
    
    def __init__(self,
                 fname='expos', #prefix for file name
                  *args, **kwargs):
        """
        Plugin: called by each button push
        """
        
        super().__init__(*args, **kwargs)
        
        self.fname=fname
        #flip the codes
        self.psmp_codes = dict(zip(self.psmp_codes.values(), self.psmp_codes.keys()))
        

        
        self.logger.debug('Rsamp.__init__ w/ feedback \'%s\''%type(self.feedback).__name__)

                
    def load_layers(self, #load data to project (for console runs)
                    rfp_l, finv_fp,
                    providerLib='ogr'
                    ):
        
        """
        special input loader for StandAlone runs"""
        log = self.logger.getChild('load_layers')
        
        
        #======================================================================
        # load rasters
        #======================================================================
        raster_d = dict()
        
        for fp in rfp_l:
            rlayer = self.load_rlay(fp)
            
            #add it in
            basefn = os.path.splitext(os.path.split(fp)[1])[0]
            raster_d[basefn] = rlayer
            
        #======================================================================
        # load finv vector layer
        #======================================================================
        fp = finv_fp
        assert os.path.exists(fp), fp
        basefn = os.path.splitext(os.path.split(fp)[1])[0]
        vlay_raw = QgsVectorLayer(fp,basefn,providerLib)
        
        
        

        # checks
        if not isinstance(vlay_raw, QgsVectorLayer): 
            raise IOError
        
        #check if this is valid
        if not vlay_raw.isValid():
            raise Error('loaded vlay \'%s\' is not valid. \n \n did you initilize?'%vlay_raw.name())
        
        #check if it has geometry
        if vlay_raw.wkbType() == 100:
            raise Error('loaded vlay has NoGeometry')
               
        
        vlay = vlay_raw
        dp = vlay.dataProvider()

        log.info('loaded vlay \'%s\' as \'%s\' %s geo  with %i feats from file: \n     %s'
                    %(vlay.name(), dp.storageType(), QgsWkbTypes().displayString(vlay.wkbType()), dp.featureCount(), fp))
        
        
        #======================================================================
        # wrap
        #======================================================================
        
        return list(raster_d.values()), vlay
    
    
    def load_rlays(self, #shortcut for loading a set of rasters in a directory
                   
                   data_dir,
                   rfn_l=None,  #if None, loads all tifs in the directory
                   
                   aoi_vlay = None,
                   logger=None,
                   **kwargs
                   ):
        #=======================================================================
        # defaults
        #=======================================================================
        if logger is None: logger=self.logger
        log=logger.getChild('load_rlays')
        
        #=======================================================================
        # prechecks
        #=======================================================================
        assert os.path.exists(data_dir)
        
        #=======================================================================
        # get filenames
        #=======================================================================
        #load all in the passed directory
        if rfn_l is None:
            rfn_l = [e for e in os.listdir(data_dir) if e.endswith('.tif')]
            log.info('scanned directory and found %i rasters: %s'%(len(rfn_l), data_dir))


        rfp_d = {fn:os.path.join(data_dir, fn) for fn in rfn_l} #get filepaths
        
        #check
        for fn, fp in rfp_d.items():
            assert os.path.exists(fp), 'bad filepath for \"%s\''%fn
        #=======================================================================
        # loop and assemble
        #=======================================================================
        log.info('loading %i rlays'%len(rfp_d))
        rlay_d = dict()
        for fn, fp in rfp_d.items():
            rlay_d[fn] = self.load_rlay(fp, logger=log,aoi_vlay=aoi_vlay, **kwargs)
            

            
            
        log.info('loaded %i'%len(rlay_d))
        
        return rlay_d
    


    def run(self, 
            rlayRaw_l, #set of rasters to sample 
            finv_raw, #inventory layer
            
            cid = None, #index field name on finv
                        
            #exposure value controls
            psmp_stat='Max', #for polygon finvs, statistic to sample
            
            #inundation sampling controls
            as_inun=False, #whether to sample for inundation (rather than wsl values)
            dtm_rlay=None, #dtm raster (for as_inun=True)
            dthresh = 0, #fordepth threshold
            clip_dtm=False,
            fname = None, #prefix for layer name
            
            ):
        """
        Generate the exposure dataset ('expos') from a set of hazard event rasters
        
        """
        
        #======================================================================
        # defaults
        #======================================================================
        log = self.logger.getChild('run')
        if cid is None: cid = self.cid
        if fname is None: fname=self.fname
        self.as_inun = as_inun
        self.finv_name = finv_raw.name() #for plotters
        log.info('executing on %i rasters'%(len(rlayRaw_l)))

        #======================================================================
        # precheck
        #======================================================================
        #assert self.crs == self.qproj.crs(), 'crs mismatch!'
        
        
        #check the finv_raw
        assert isinstance(finv_raw, QgsVectorLayer), 'bad type on finv_raw'
        """rasteres are checked below"""
        assert finv_raw.crs() == self.qproj.crs(), 'finv_raw crs %s doesnt match projects \'%s\'' \
            %(finv_raw.crs().authid(), self.qproj.crs().authid())
        assert cid in [field.name() for field in finv_raw.fields()], \
            'requested cid field \'%s\' not found on the finv_raw'%cid
            


        
        #check the rasters
        rname_l = []
        for rlay in rlayRaw_l:
            assert isinstance(rlay, QgsRasterLayer)

            assert rlay.crs() == self.qproj.crs(), 'rlay %s crs doesnt match project'%(rlay.name())
            rname_l.append(rlay.name())
        
        self.rname_l = rname_l
        
        #======================================================================
        # prep the finv for sampling
        #======================================================================
        self.finv_name = finv_raw.name()
        
        #drop all the fields except the cid
        finv = self.deletecolumn(finv_raw, [cid], invert=True)
        
        
        #fix the geometry
        finv = self.fixgeometries(finv, logger=log)
        

        #check field lengths
        self.finv_fcnt = len(finv.fields())
        assert self.finv_fcnt== 1, 'failed to drop all the fields'
        
        self.gtype = QgsWkbTypes().displayString(finv.wkbType())
        
        if self.gtype.endswith('Z'):
            log.warning('passed finv has Z values... these are not supported')
            
        self.feedback.setProgress(20)
        #=======================================================================
        # prep the raster layers------
        #=======================================================================

        self.feedback.setProgress(40)
        #=======================================================================
        #inundation runs--------
        #=======================================================================
        if as_inun:
            #===================================================================
            # #prep DTM
            #===================================================================
            if clip_dtm:
                
                """makes the raster clipping a bitcleaner
                
                2020-05-06
                ran 2 tests, and this INCREASED run times by ~20%
                set default to clip_dtm=False
                """
                log.info('trimming dtm \'%s\' by finv extents'%(dtm_rlay.name()))
                finv_buf = self.polygonfromlayerextent(finv,
                                        round_to=dtm_rlay.rasterUnitsPerPixelX()*3,#buffer by 3x the pixel size
                                         logger=log )
        
                
                #clip to just the polygons
                dtm_rlay1 = self.cliprasterwithpolygon(dtm_rlay,finv_buf, logger=log)
            else:
                dtm_rlay1 = dtm_rlay
                
            self.feedback.setProgress(60)
            #===================================================================
            # sample by goetype
            #===================================================================
            if 'Polygon' in self.gtype:
                res_vlay = self.samp_inun(finv,rlayRaw_l, dtm_rlay1, dthresh)
            elif 'Line' in self.gtype:
                res_vlay = self.samp_inun_line(finv, rlayRaw_l, dtm_rlay1, dthresh)
            else:
                raise Error('\'%s\' got unexpected gtype: %s'%(finv.name(), self.gtype))
            
            res_name = '%s_%s_%i_%i_d%.2f'%(
                fname, self.tag, len(rlayRaw_l), res_vlay.dataProvider().featureCount(), dthresh)
        
        #=======================================================================
        #WSL value sampler------
        #=======================================================================
        else:
            res_vlay = self.samp_vals(finv,rlayRaw_l, psmp_stat)
            res_name = '%s_%s_%i_%i'%(fname, self.tag, len(rlayRaw_l), res_vlay.dataProvider().featureCount())
            
            if not 'Point' in self.gtype: res_name = res_name + '_%s'%psmp_stat.lower()
            
        res_vlay.setName(res_name)
        #=======================================================================
        # wrap
        #=======================================================================
        """TODO: harmonize output types for build modules"""
        #get dataframe like results
        try:
            df = vlay_get_fdf(res_vlay, logger=log).set_index(cid, drop=True
                                          ).rename(columns=self.names_d)
            """
            view(df)
            d.keys()
            """
            #get sorted index by values
            sum_ser = pd.Series({k:cser.dropna().sum() for k, cser in df.items()}).sort_values()
            
            #set this new index
            self.res_df = df.loc[:, sum_ser.index]
        
        except Exception as e:
            log.warning('failed to convert vlay to dataframe w/ \n    %s'%e)
        
        #max out the progress bar
        self.feedback.setProgress(90)

        log.info('sampling finished')
        
        self.psmp_stat=psmp_stat #set for val_str
        
        return res_vlay
    

    def runPrep(self, #apply raster preparation handels to a set of rasters
                rlayRaw_l,
                **kwargs
                ):
        
        #=======================================================================
        # do the prep
        #=======================================================================
        self.feedback.setProgress(20)
        res_l = []
        for rlayRaw in rlayRaw_l:
            rlay = self.prep(rlayRaw, **kwargs)
            res_l.append(rlay)
            
            self.feedback.upd_prog(70/len(rlayRaw_l), method='append')
            assert isinstance(rlay, QgsRasterLayer)
            
        self.feedback.setProgress(90)
            
        return res_l
            
        
    def prep(self, #prepare a raster for sampling
             rlayRaw, #set of raw raster to apply prep handles to
             allow_download=False,
             aoi_vlay=None,
             
             allow_rproj=False,
             
             clip_rlays=False,
             
             scaleFactor=1.00,
             logger=None,
             
             ):
        """
        #=======================================================================
        # mstore
        #=======================================================================
        todo: need to fix this... using the store is currently crashing Qgis
        
        """
        #=======================================================================
        # defaults
        #=======================================================================
        if logger is None: logger=self.logger
        log = logger.getChild('prep')
        
        log.info('on \'%s\''%rlayRaw.name())

        res_d = dict() #reporting container
        
        #start a new store for handling  intermediate layers
        #mstore = QgsMapLayerStore()
        
        newLayerName='%s_prepd' % rlayRaw.name()
        
        #=======================================================================
        # precheck
        #=======================================================================
        #check the aoi
        if clip_rlays: assert isinstance(aoi_vlay, QgsVectorLayer)
        if not aoi_vlay is None:
            self.check_aoi(aoi_vlay)
        
        #=======================================================================
        # dataProvider check/conversion-----
        #=======================================================================
        if not rlayRaw.providerType() == 'gdal':
            msg = 'raster \'%s\' providerType = \'%s\' and allow_download=%s' % (
                rlayRaw.name(), rlayRaw.providerType(), allow_download)
            #check if we're allowed to fix
            if not allow_download:
                raise Error(msg)
            log.info(msg)
            #set extents
            if not aoi_vlay is None: #aoi extents in correct CRS
                extent = QgsCoordinateTransform(aoi_vlay.crs(), rlayRaw.crs(), 
                                                self.qproj.transformContext()
                                                ).transformBoundingBox(aoi_vlay.extent())
            else:
                extent = rlayRaw.extent() #layers extents
            #save a local copy
            ofp = self.write_rlay(rlayRaw, extent=extent, 
                newLayerName='%s_gdal' % rlayRaw.name(),
                out_dir =  os.environ['TEMP'], #will write to the working directory at the end
                logger=log)
            #load this file
            rlayDp = self.load_rlay(ofp, logger=log)
            #check
            assert rlayDp.bandCount() == rlayRaw.bandCount()
            assert rlayDp.providerType() == 'gdal'
            
            res_d['download'] = 'from \'%s\' to \'gdal\''%rlayRaw.providerType()
            
            self.mstore.addMapLayer(rlayRaw)

        else:
            rlayDp = rlayRaw
            log.debug('%s has expected dataProvider \'gdal\''%rlayRaw.name())

        #=======================================================================
        # re-projection--------
        #=======================================================================
        if not rlayDp.crs() == self.qproj.crs():
            msg = 'raster \'%s\' crs = \'%s\' and allow_rproj=%s' % (
                rlayDp.name(), rlayDp.crs(), allow_rproj)
            if not allow_rproj:
                raise Error(msg)
            log.info(msg)
            #save a local copy?
            newName = '%s_%s' % (rlayDp.name(), self.qproj.crs().authid()[5:])
            
            """just write at the end
            if allow_download:
                output = os.path.join(self.out_dir, '%s.tif' % newName)
            else:
                output = 'TEMPORARY_OUTPUT'"""
            output = 'TEMPORARY_OUTPUT'
            #change the projection
            rlayProj = self.warpreproject(rlayDp, crsOut=self.qproj.crs(), 
                output=output, layname=newName)
            
            res_d['rproj'] = 'from %s to %s'%(rlayDp.crs().authid(), self.qproj.crs().authid())
            self.mstore.addMapLayer(rlayDp)

        else:
            log.debug('\'%s\' crs matches project crs: %s'%(rlayDp.name(), rlayDp.crs()))
            rlayProj = rlayDp
            
        #=======================================================================
        # aoi slice----
        #=======================================================================
        if clip_rlays:
            log.debug('trimming raster %s by AOI'%rlayRaw.name())
            log.warning('not Tested!')
            
            #clip to just the polygons
            rlayTrim = self.cliprasterwithpolygon(rlayProj,aoi_vlay, logger=log)
            
            res_d['clip'] = 'with \'%s\''%aoi_vlay.name()
            self.mstore.addMapLayer(rlayProj)
        else:
            rlayTrim = rlayProj
            
        #===================================================================
        # scale
        #===================================================================
        
        if not float(scaleFactor) ==float(1.00):
            rlayScale = self.raster_mult(rlayTrim, scaleFactor, logger=log)
            
            res_d['scale'] = 'by %.4f'%scaleFactor
            self.mstore.addMapLayer(rlayTrim)
        else:
            rlayScale = rlayTrim
            
        #=======================================================================
        # final write
        #=======================================================================
        resLay1 = rlayScale
        write=False
        
        if len(res_d)>0: #only where we did some operations
            write=True
            
        """write it regardless
        if len(res_d)==1 and 'download' in res_d.keys():
            write=False"""
            
        
        
        if write:
            resLay1.setName(newLayerName)
            ofp = self.write_rlay(resLay1, logger=log)
            
            #mstore.addMapLayer(resLay1)
            
            #use the filestore layer
            resLay = self.load_rlay(ofp, logger=log)
            """control canvas loading in the plugin"""
            
        else:
            log.warning('layer \'%s\' not written to file!'%resLay.name())
            resLay=resLay1
            

        #=======================================================================
        # wrap
        #=======================================================================
        
        
        
        log.info('finished w/ %i prep operations on \'%s\' \n    %s'%(
            len(res_d), resLay.name(), res_d))
        
        #clean up the store
        #=======================================================================
        # _ = mstore.takeMapLayer(rlayRaw) #take out the raw (without deleteing) 
        # try:
        #     _ = mstore.takeMapLayer(resLay) #try and pull out the result layer
        # except:
        #     log.warning('failed to remove \'%s\' from store'%resLay.name())
        #=======================================================================
        
        """
        for k,v in mstore.mapLayers().items():
            print(k,v)
        
        """
        #self.mstore.removeAllMapLayers() #clear all layers
        assert isinstance(resLay, QgsRasterLayer)
        
        
        return resLay
    


        
        
    def samp_vals(self, #sample a set of rasters with a vectorlayer
                  finv, raster_l,psmp_stat):
        """
        this is NOT for inundation percent
        can handle all 3 geometries"""
        
        log = self.logger.getChild('samp_vals')
        #=======================================================================
        # build the loop
        #=======================================================================
        gtype=self.gtype
        if 'Polygon' in gtype: 
            assert psmp_stat in self.psmp_codes, 'unrecognized psmp_stat' 
            psmp_code = self.psmp_codes[psmp_stat] #sample each raster
            algo_nm = 'qgis:zonalstatistics'

            
            
        elif 'Point' in gtype:
            algo_nm = 'qgis:rastersampling'
            
        elif 'Line' in gtype:
            algo_nm = 'native:pointsalonglines'
        else:
            raise Error('unsupported gtype: %s'%gtype)

        #=======================================================================
        # sample loop
        #=======================================================================
        names_d = dict()
        
        log.info('sampling %i raster layers w/ algo \'%s\' and gtype: %s'%(
            len(raster_l), algo_nm, gtype))
        
        for indxr, rlay in enumerate(raster_l):
            
            log.info('%i/%i sampling \'%s\' on \'%s\''%(
                indxr+1, len(raster_l), finv.name(), rlay.name()))
            
            ofnl =  [field.name() for field in finv.fields()]
            self.mstore.addMapLayer(finv)
            #===================================================================
            # sample.poly----------
            #===================================================================
            if 'Polygon' in gtype: 
                
                algo_nm = 'native:zonalstatisticsfb'
            
                ins_d = {       'COLUMN_PREFIX':indxr, 
                                'INPUT_RASTER':rlay, 
                                'INPUT':finv, 
                                'RASTER_BAND':1, 
                                'STATISTICS':[psmp_code],#0: pixel counts, 1: sum
                                'OUTPUT' : 'TEMPORARY_OUTPUT',
                                }
                    
                #execute the algo
                res_d = processing.run(algo_nm, ins_d, feedback=self.feedback)
                
                
                finv = res_d['OUTPUT']
        
            #=======================================================================
            # sample.Line--------------
            #=======================================================================
            elif 'Line' in gtype: 
                finv = self.line_sample_stats(finv, rlay,[psmp_stat], logger=log)
            #======================================================================
            # sample.Points----------------
            #======================================================================
            elif 'Point' in gtype: 
                #build the algo params
                params_d = { 'COLUMN_PREFIX' : rlay.name(),
                             'INPUT' : finv,
                              'OUTPUT' : 'TEMPORARY_OUTPUT',
                               'RASTERCOPY' : rlay}
                
                #execute the algo
                res_d = processing.run(algo_nm, params_d, feedback=self.feedback)
        
                #extract and clean results
                finv = res_d['OUTPUT']

            else:
                raise Error('unexpected geo type: %s'%gtype)
            
            #===================================================================
            # sample.wrap
            #===================================================================
            assert isinstance(finv, QgsVectorLayer)
            assert len(finv.fields()) == self.finv_fcnt + indxr +1, \
                'bad field length on %i'%indxr
                
            finv.setName('%s_%i'%(self.finv_name, indxr))
            
            #===================================================================
            # correct field names
            #===================================================================
            """
            algos don't assign good field names.
            collecting a conversion dictionary then adjusting below
            
            TODO: propagate these field renames to the loaded result layers
            """
            #get/updarte the field names
            nfnl =  [field.name() for field in finv.fields()]
            new_fn = set(nfnl).difference(ofnl) #new field names not in the old
            
            if len(new_fn) > 1:
                raise Error('bad mismatch: %i \n    %s'%(len(new_fn), new_fn))
            elif len(new_fn) == 1:
                names_d[list(new_fn)[0]] = rlay.name()
            else:
                raise Error('bad fn match')
                 
                
            log.debug('sampled %i values on raster \'%s\''%(
                finv.dataProvider().featureCount(), rlay.name()))
            
        self.names_d = names_d #needed by write()
        
        log.debug('finished w/ \n%s'%self.names_d)
        
        return finv
    """
    view(finv)
    """
    
    def samp_inun(self, #inundation percent for polygons
                  finv, raster_l, dtm_rlay, dthresh,
                   ):

        #=======================================================================
        # defaults
        #=======================================================================
        log = self.logger.getChild('samp_inun')
        gtype=self.gtype
        
        #setup temp dir
        import tempfile #todo: move this up top
        temp_dir = tempfile.mkdtemp()        
        #=======================================================================
        # precheck
        #=======================================================================
        dp = finv.dataProvider()

        assert isinstance(dtm_rlay, QgsRasterLayer)
        assert isinstance(dthresh, float)
        assert 'Memory' in dp.storageType() #zonal stats makes direct edits
        assert 'Polygon' in gtype

        #=======================================================================
        # sample loop---------
        #=======================================================================
        """
        too memory intensive to handle writing of all these.
        an advanced user could retrive from the working folder if desiered
        """
        names_d = dict()
        parea_d = dict()
        for indxr, rlay in enumerate(raster_l):
            log = self.logger.getChild('samp_inun.%s'%rlay.name())
            ofnl = [field.name() for field in finv.fields()]

            #===================================================================
            # #get depth raster
            #===================================================================
            dep_rlay = self._get_depr(dtm_rlay, log, temp_dir, rlay)
            
            #===================================================================
            # get threshold
            #===================================================================
            #reduce to all values above depththreshold
            log.info('calculating %.2f threshold raster'%dthresh) 
            
            """
            TODO: speed this up somehow... super slow
                native calculator?
                
            """
            
            thr_rlay = self.grastercalculator(
                                'A*(A>%.2f)'%dthresh, #null if not above minval
                               {'A':dep_rlay},
                               logger=log,
                               layname= '%s_mv'%dep_rlay.name()
                               )
        
            #===================================================================
            # #get cell counts per polygon
            #===================================================================
            log.info('getting pixel counts on %i polys'%finv.dataProvider().featureCount())
            
            algo_nm = 'native:zonalstatisticsfb'
            
            ins_d = {       'COLUMN_PREFIX':indxr, 
                            'INPUT_RASTER':thr_rlay, 
                            'INPUT':finv, 
                            'RASTER_BAND':1, 
                            'STATISTICS':[0],#0: pixel counts, 1: sum
                            'OUTPUT' : 'TEMPORARY_OUTPUT',
                            }
                
            #execute the algo
            res_d = processing.run(algo_nm, ins_d, feedback=self.feedback)
            
            finvw = res_d['OUTPUT']
            """
            view(finvw)
            view(finv)
            """

            #===================================================================
            # check/correct field names
            #===================================================================
            #get/updarte the field names
            nfnl =  [field.name() for field in finvw.fields()]
            new_fn = set(nfnl).difference(ofnl) #new field names not in the old
             
            if len(new_fn) > 1:
                """
                possible error with algo changes
                """
                raise Error('zonalstatistics generated more new fields than expected: %i \n    %s'%(
                    len(new_fn), new_fn))
            elif len(new_fn) == 1:
                names_d[list(new_fn)[0]] = rlay.name()
            else:
                raise Error('bad fn match')
            
            #===================================================================
            # #clean up the layers
            #===================================================================
            self.mstore.addMapLayer(finv)
            self.mstore.removeMapLayer(finv)
            finv = finvw
            
            
            #===================================================================
            # update pixel size
            #===================================================================
            parea_d[rlay.name()] = rlay.rasterUnitsPerPixelX()*rlay.rasterUnitsPerPixelY()
            
        #=======================================================================
        # area calc-----------
        #=======================================================================
        log = self.logger.getChild('samp_inun')
        log.info('calculating areas on %i results fields:\n    %s'%(len(names_d), list(names_d.keys())))
        
        #add geometry fields
        finv = self.addgeometrycolumns(finv, logger = log)
        
        #get data frame
        df_raw  = vlay_get_fdf(finv, logger=log)
        """
        view(df_raw)
        """
        df = df_raw.rename(columns=names_d)

        #multiply each column by corresponding raster's cell size
        res_df = df.loc[:, names_d.values()].multiply(pd.Series(parea_d)).round(self.prec)
        res_df = res_df.rename(columns={coln:'%s_a'%coln for coln in res_df.columns})
        
        #divide by area of each polygon
        frac_df = res_df.div(df_raw['area'], axis=0).round(self.prec)
        d = {coln:'%s_pct_raw'%coln for coln in frac_df.columns}
        frac_df = frac_df.rename(columns=d)
        res_df = res_df.join(frac_df)#add back in results
        
        #adjust for excessive fractions
        booldf = frac_df>1
        d1 = {coln:'%s_pct'%ename for ename, coln in d.items()}
        if booldf.any().any():
            log.warning('got %i (of %i) pct values >1.00. setting to 1.0 (bad pixel/polygon ratio?)'%(
                booldf.sum().sum(), booldf.size))
            
            fix_df = frac_df.where(~booldf, 1.0)
            fix_df = fix_df.rename(columns=d1)
            res_df = res_df.join(fix_df)
            
        else:
            res_df = res_df.rename(columns=d1)
        
        #add back in all the raw
        res_df = res_df.join(df_raw.rename(columns=names_d))
        
        #set the reuslts converter
        self.names_d = {coln:ename for coln, ename in dict(zip(d1.values(), names_d.values())).items()}
        
        #=======================================================================
        # write working reuslts
        #=======================================================================
        ofp = os.path.join(temp_dir, 'RAW_rsamp_SampInun_%s_%.2f.csv'%(self.tag, dthresh))
        res_df.to_csv(ofp, index=None)
        log.info('wrote working data to \n    %s'%ofp)
        
        #slice to results only
        res_df = res_df.loc[:,[self.cid]+list(d1.values())]
        
        log.info('data assembed w/ %s: \n    %s'%(str(res_df.shape), res_df.columns.tolist()))
        
        #=======================================================================
        # bundle back into vectorlayer
        #=======================================================================
        geo_d = vlay_get_fdata(finv, geo_obj=True, logger=log)
        res_vlay = self.vlay_new_df2(res_df, crs=finv.crs(), geo_d=geo_d, logger=log,
                               layname='%s_%s_inun'%(self.tag, finv.name()))
        
        log.info('finisished w/ %s'%res_vlay.name())

        
        return res_vlay




    def samp_inun_line(self, #inundation percent for Line

                  finv, raster_l, dtm_rlay, dthresh,
                   ):
        
        """"
        couldn't find a great pre-made algo
        
        option 1:
            SAGA profile from lines (does not retain line attributes)
            join attributes by nearest (to retrieve XID)
            
        option 2:
            Generate points (pixel centroids) along line 
                (does not retain line attributes)
                generates points on null pixel values
            sample points
            join by nearest
            
        option 3:
            add geometry attributes
            Points along geometry (retains attribute)
            sample raster
            count those above threshold
            divide by total for each line
            get % above threshold for each line
            get km inundated for each line
        
        """
        #=======================================================================
        # defaults
        #=======================================================================
        log = self.logger.getChild('samp_inun_line')
        gtype=self.gtype
        
        #setup temp dir
        import tempfile #todo: move this up top
        temp_dir = tempfile.mkdtemp()        
        #=======================================================================
        # precheck
        #=======================================================================
        dp = finv.dataProvider()

        assert isinstance(dtm_rlay, QgsRasterLayer)
        assert isinstance(dthresh, float), 'expected float for dthresh. got %s'%type(dthresh)
        assert 'Memory' in dp.storageType() #zonal stats makes direct edits
        assert 'Line' in gtype

        #=======================================================================
        # sample loop---------
        #=======================================================================
        """
        too memory intensive to handle writing of all these.
        an advanced user could retrive from the working folder if desiered
        """
        names_d = dict()

        for indxr, rlay in enumerate(raster_l):
            log = self.logger.getChild('samp_inunL.%s'%rlay.name())
            ofnl = [field.name() for field in finv.fields()]


            #===================================================================
            # #get depth raster
            #===================================================================
            dep_rlay = self._get_depr(dtm_rlay, log, temp_dir, rlay)
            
            #===============================================================
            # #convert to points
            #===============================================================
            params_d = { 'DISTANCE' : dep_rlay.rasterUnitsPerPixelX(), 
                        'END_OFFSET' : 0, 
                        'INPUT' : finv, 
                        'OUTPUT' : 'TEMPORARY_OUTPUT', 
                        'START_OFFSET' : 0 }
            
    
            res_d = processing.run('native:pointsalonglines', params_d, feedback=self.feedback)
            fpts_vlay = res_d['OUTPUT']
            
            #===============================================================
            # #sample the raster
            #===============================================================
            ofnl2 = [field.name() for field in fpts_vlay.fields()]
            params_d = { 'COLUMN_PREFIX' : rlay.name(),
                         'INPUT' : fpts_vlay,
                          'OUTPUT' : 'TEMPORARY_OUTPUT',
                           'RASTERCOPY' : dep_rlay}
            
            res_d = processing.run('qgis:rastersampling', params_d, feedback=self.feedback)
            fpts_vlay = res_d['OUTPUT']

            #get new field name
            new_fn = set([field.name() for field in fpts_vlay.fields()]).difference(ofnl2) #new field names not in the old
            
            assert len(new_fn)==1
            new_fn = list(new_fn)[0]
            
            #===================================================================
            # clean/pull data
            #===================================================================
            #drop all the other fields
            fpts_vlay = self.deletecolumn(fpts_vlay,[new_fn, self.cid], invert=True, logger=log )
            
            #pull data
            """
            the builtin statistics algo doesn't do a good job handling nulls
            """
            pts_df = vlay_get_fdf(fpts_vlay, logger=log)
            
            #===================================================================
            # calc stats
            #===================================================================
            #set those below threshold to null
            boolidx = pts_df[new_fn]<=dthresh
            
            pts_df.loc[boolidx, new_fn] = np.nan
            log.debug('set %i (of %i) \'%s\' vals <= %.2f to null'%(
                boolidx.sum(), len(boolidx), new_fn, dthresh))
            """
            view(pts_df)
            (pts_df[self.cid]==4).sum()
            """
            #get count of REAL values in each xid group
            pts_df['all']=1 #add dummy column for the demoninator
            sdf = pts_df.groupby(self.cid).count().reset_index(drop=False).rename(
                columns={new_fn:'real'})
            
            #get ratio (non-NAN count / all count)
            new_fn = rlay.name()
            sdf[new_fn] = sdf['real'].divide(sdf['all']).round(self.prec)
            
            assert sdf[new_fn].max() <=1
            #===================================================================
            # link in result
            #===================================================================
            #convert df back to a mlay
            pstat_vlay = self.vlay_new_df2(sdf.drop(['all', 'real'], axis=1),
                                            layname='%s_stats'%(finv.name()), logger=log)

            
            #join w/ algo
            params_d = { 'DISCARD_NONMATCHING' : False,
                         'FIELD' : self.cid, 
                         'FIELDS_TO_COPY' : [new_fn],
                         'FIELD_2' : self.cid,
                          'INPUT' : finv,
                          'INPUT_2' : pstat_vlay,
                         'METHOD' : 1, #Take attributes of the first matching feature only (one-to-one)
                          'OUTPUT' : 'TEMPORARY_OUTPUT',
                           'PREFIX' : ''}
            
            res_d = processing.run('native:joinattributestable', params_d, feedback=self.feedback)
            finv = res_d['OUTPUT']

            #===================================================================
            # check/correct field names
            #===================================================================
            """
            algos don't assign good field names.
            collecting a conversion dictionary then adjusting below
            """
            #get/updarte the field names
            nfnl =  [field.name() for field in finv.fields()]
            new_fn = set(nfnl).difference(ofnl) #new field names not in the old
            
            if len(new_fn) > 1:
                raise Error('unexpected algo behavior... bad new field count: %s'%new_fn)
            elif len(new_fn) == 1:
                names_d[list(new_fn)[0]] = rlay.name()
                log.debug('updated names_d w/ %s'%rlay.name())
            else:
                raise Error('bad fn match')
        #=======================================================================
        # wrap-------------
        #=======================================================================
        self.names_d = dict() #names should be fine
        log.debug('finished')
        """
        view(finv)
        """

        return finv
    
    def _get_depr(self, #get a depth raster, but first check if its already been made
                  dtm_rlay, log, temp_dir, rlay):
        
        dep_rlay_nm = '%s_%s' % (dtm_rlay.name(), rlay.name())
        
        #pull previously created
        if dep_rlay_nm in self.dep_rlay_d:
            dep_rlay = self.dep_rlay_d[dep_rlay_nm]
            
        #build fresh
        else:
            log.info('calculating depth raster \'%s\''%dep_rlay_nm)
            
            #using Qgis raster calculator constructor
            dep_rlay = self.raster_subtract(rlay, dtm_rlay, logger=log, 
                out_dir=os.path.join(temp_dir, 'dep'), 
                layname=dep_rlay_nm)
            
            #store for next time
            self.dep_rlay_d[dep_rlay_nm] = dep_rlay
            
        return dep_rlay
    
    def raster_subtract(self, #performs raster calculator rlayBig - rlaySmall
                        rlayBig, rlaySmall,
                        out_dir = None,
                        layname = None,
                        logger = None,
                        ):
        
        #=======================================================================
        # defaults
        #=======================================================================
        if logger is None: logger =  self.logger
        log = self.logger.getChild('raster_subtract')
        
        if out_dir is None:
            out_dir = os.environ['TEMP']
            
        if layname is None:
            layname = '%s_dep'%rlayBig.name()
        
        #=======================================================================
        # assemble the entries
        #=======================================================================
        entries_d = dict() 

        for tag, rlay in {'Big':rlayBig, 'Small':rlaySmall}.items():
            rcentry = QgsRasterCalculatorEntry()
            rcentry.raster=rlay
            rcentry.ref = '%s@1'%tag
            rcentry.bandNumber=1
            entries_d[tag] = rcentry

            
        #=======================================================================
        # assemble parameters
        #=======================================================================
        formula = '%s - %s'%(entries_d['Big'].ref, entries_d['Small'].ref)
        outputFile = os.path.join(out_dir, '%s.tif'%layname)
        outputExtent  = rlayBig.extent()
        outputFormat = 'GTiff'
        nOutputColumns = rlayBig.width()
        nOutputRows = rlayBig.height()
        rasterEntries =list(entries_d.values())
        

        #=======================================================================
        # precheck
        #=======================================================================
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
            
        if os.path.exists(outputFile):
            msg = 'requseted outputFile exists: %s'%outputFile
            if self.overwrite:
                log.warning(msg)
                os.remove(outputFile)
            else:
                raise Error(msg)
            
            
        assert not os.path.exists(outputFile), 'requested outputFile already exists! \n    %s'%outputFile
        
        #=======================================================================
        # execute
        #=======================================================================
        """throwing depreciation warning"""
        rcalc = QgsRasterCalculator(formula, outputFile, outputFormat, outputExtent,
                            nOutputColumns, nOutputRows, rasterEntries)
        
        result = rcalc.processCalculation(feedback=self.feedback)
        
        #=======================================================================
        # check    
        #=======================================================================
        if not result == 0:
            raise Error(rcalc.lastError())
        
        assert os.path.exists(outputFile)
        
        
        log.info('saved result to: \n    %s'%outputFile)
            
        #=======================================================================
        # retrieve result
        #=======================================================================
        rlay = QgsRasterLayer(outputFile, layname)
        
        return rlay
    
    def raster_mult(self, #performs raster calculator rlayBig - rlaySmall
                        rlayRaw,
                        scaleFactor,
                        out_dir = None,
                        layname = None,
                        logger = None,
                        ):
        
        #=======================================================================
        # defaults
        #=======================================================================
        if logger is None: logger =  self.logger
        log = self.logger.getChild('raster_mult')
        
        if out_dir is None:
            out_dir = os.environ['TEMP']
            
        if layname is None:
            layname = '%s_scaled'%rlayRaw.name()
            
        #=======================================================================
        # precheck
        #=======================================================================
        assert scaleFactor >= 0.01, 'scaleFactor = %.2f is too low'%scaleFactor
        assert round(scaleFactor, 4)!=round(1.0, 4), 'scaleFactor = 1.0'
        
        #=======================================================================
        # assemble the entries
        #=======================================================================
        entries_d = dict() 

        for tag, rlay in {'rlayRaw':rlayRaw}.items():
            rcentry = QgsRasterCalculatorEntry()
            rcentry.raster=rlay
            rcentry.ref = '%s@1'%tag
            rcentry.bandNumber=1
            entries_d[tag] = rcentry

            
        #=======================================================================
        # assemble parameters
        #=======================================================================
        formula = '%s * %.2f'%(entries_d['rlayRaw'].ref, scaleFactor)
        outputFile = os.path.join(out_dir, '%s.tif'%layname)
        outputExtent  = rlayRaw.extent()
        outputFormat = 'GTiff'
        nOutputColumns = rlayRaw.width()
        nOutputRows = rlayRaw.height()
        rasterEntries =list(entries_d.values())
        

        #=======================================================================
        # precheck
        #=======================================================================
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
            
        if os.path.exists(outputFile):
            msg = 'requseted outputFile exists: %s'%outputFile
            if self.overwrite:
                log.warning(msg)
                os.remove(outputFile)
            else:
                raise Error(msg)
            
            
        assert not os.path.exists(outputFile), 'requested outputFile already exists! \n    %s'%outputFile
        
        #=======================================================================
        # execute
        #=======================================================================
        """throwing depreciation warning"""
        rcalc = QgsRasterCalculator(formula, outputFile, outputFormat, outputExtent,
                            nOutputColumns, nOutputRows, rasterEntries)
        
        result = rcalc.processCalculation(feedback=self.feedback)
        
        #=======================================================================
        # check    
        #=======================================================================
        if not result == 0:
            raise Error(rcalc.lastError())
        
        assert os.path.exists(outputFile)
        
        
        log.info('saved result to: \n    %s'%outputFile)
            
        #=======================================================================
        # retrieve result
        #=======================================================================
        rlay = QgsRasterLayer(outputFile, layname)
        
        return rlay
        
        

        
    def line_sample_stats(self, #get raster stats using a line
                    line_vlay, #line vectorylayer with geometry to sample from
                    rlay, #raster to sample
                    sample_stats, #list of stats to sample
                    logger=None,
                    ):
        """
        sampliung a raster layer with a line and a statistic
        
        TODO: check if using the following is faster:
            Densify by Interval
            Drape
            Extract Z
        """
        if logger is None: logger=self.logger
        log=logger.getChild('line_sample_stats')
        log.debug('on %s'%(line_vlay.name()))
        
        #drop everythin gto lower case
        sample_stats = [e.lower() for e in sample_stats]
        #===============================================================
        # #convert to points
        #===============================================================
        params_d = { 'DISTANCE' : rlay.rasterUnitsPerPixelX(), 
                    'END_OFFSET' : 0, 
                    'INPUT' : line_vlay, 
                    'OUTPUT' : 'TEMPORARY_OUTPUT', 
                    'START_OFFSET' : 0 }
        

        res_d = processing.run('native:pointsalonglines', params_d, feedback=self.feedback)
        fpts_vlay = res_d['OUTPUT']
        
        #===============================================================
        # #sample the raster
        #===============================================================
        ofnl2 = [field.name() for field in fpts_vlay.fields()]
        params_d = { 'COLUMN_PREFIX' : rlay.name(),
                     'INPUT' : fpts_vlay,
                      'OUTPUT' : 'TEMPORARY_OUTPUT',
                       'RASTERCOPY' : rlay}
        

        res_d = processing.run('qgis:rastersampling', params_d, feedback=self.feedback)
        fpts_vlay = res_d['OUTPUT']
        """
        view(fpts_vlay)
        """
        #get new field name
        new_fn = set([field.name() for field in fpts_vlay.fields()]).difference(ofnl2) #new field names not in the old
        
        assert len(new_fn)==1
        new_fn = list(new_fn)[0]
        
        #===============================================================
        # get stats
        #===============================================================
        """note this does not return xid values where everything sampled as null"""
        params_d = { 'CATEGORIES_FIELD_NAME' : [self.cid], 
                    'INPUT' : fpts_vlay,
                    'OUTPUT' : 'TEMPORARY_OUTPUT', 
                    'VALUES_FIELD_NAME' :new_fn}
        
        res_d = processing.run('qgis:statisticsbycategories', params_d, feedback=self.feedback)
        stat_tbl = res_d['OUTPUT']
        
        #===============================================================
        # join stats back to line_vlay
        #===============================================================
        #check that the sample stat is in there
        s = set(sample_stats).difference([field.name() for field in stat_tbl.fields()])
        assert len(s)==0, 'requested sample statistics \"%s\' failed to generate'%s 
        
        #run algo
        params_d = { 'DISCARD_NONMATCHING' : False,
                     'FIELD' : self.cid, 
                     'FIELDS_TO_COPY' : sample_stats,
                     'FIELD_2' : self.cid,
                      'INPUT' : line_vlay,
                      'INPUT_2' : stat_tbl,
                     'METHOD' : 1, #Take attributes of the first matching feature only (one-to-one)
                      'OUTPUT' : 'TEMPORARY_OUTPUT',
                       'PREFIX' : line_vlay }
        
        res_d = processing.run('native:joinattributestable', params_d, feedback=self.feedback)
        line_vlay = res_d['OUTPUT']
        
        log.debug('finished on %s w/ %i'%(line_vlay.name(), len(line_vlay)))
        

        return line_vlay
        
    #===========================================================================
    # CHECKS--------
    #===========================================================================
    def check(self):
        pass
    
    def dtm_check(self, vlay):
        
        log = self.logger.getChild('dtm_check')
        
        df = vlay_get_fdf(vlay)
        
        boolidx = df.isna()
        if boolidx.any().any():
            log.error('got %i (of %i) nulls on dtm sampler'%(boolidx.sum().sum(), len(boolidx)))
        
        log.info('passed checks')
        
    #===========================================================================
    # OUTPUTS--------
    #===========================================================================
    def write_res(self, #save expos dataset to file
                  vlay,
              out_dir = None, #directory for puts
              names_d = None, #names conversion
              rname_l = None,
              res_name = None, #prefix for output name
              write=True,
              ):
        
        log = self.logger.getChild('write_res')
        #======================================================================
        # defaults
        #======================================================================
        if names_d is None: names_d = self.names_d
        if rname_l is None: rname_l = self.rname_l
        if out_dir is None: out_dir = self.out_dir
        if res_name is None: res_name = vlay.name()
        log.debug("on \'%s\'"%res_name)
        #======================================================================
        # prechekss
        #======================================================================
        assert os.path.exists(out_dir), 'bad out_dir'
        #======================================================================
        # get
        #======================================================================
        #extract data
        df = vlay_get_fdf(vlay)
        
        #rename
        if len(names_d) > 0:
            df = df.rename(columns=names_d)
            log.info('renaming columns: \n    names_d: %s \n    df.cols:%s'%(
                names_d, df.columns.tolist()))
            

        #check the raster names
        miss_l = set(rname_l).difference(df.columns.to_list())
        if len(miss_l)>0:
            log.warning('failed to map %i raster layer names onto results: \n    %s'%(len(miss_l), miss_l))
        
        df =  df.set_index(self.cid, drop=True)
        #=======================================================================
        # write
        #=======================================================================
        if not write: 
            return df
        out_fp = self.output_df(df, '%s.csv'%res_name, out_dir = out_dir, write_index=True)
        
        self.out_fp = out_fp
        
        return df


    def update_cf(self, cf_fp): #configured control file updater
        """make sure you write the file first"""
        return self.set_cf_pars(
            {
            'dmg_fps':(
                {'expos':self.out_fp}, 
                '#\'expos\' file path set from rsamp.py at %s'%(datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')),
                ),
            'parameters':(
                {'as_inun':str(self.as_inun)},
                )
             },
            cf_fp = cf_fp
            )
        
        
    def upd_cf_dtm(self, cf_fp=None):
        if cf_fp is None: cf_fp=self.cf_fp
        return self.set_cf_pars(
            {
            'dmg_fps':(
                {'gels':self.out_fp},
                '#\'gels\' file path set from rsamp.py at %s'%(datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')),
                    ),
            'parameters':(
                {'felv':'ground'},
                )
             },
            cf_fp = cf_fp
            )
        
    #===========================================================================
    # PLOTS-----
    #===========================================================================
    def plot_hist(self, #plot failure histogram of all layers
                      df=None,**kwargs): 

        if df is None: df=self.res_df
        title = '%s Raster Sample Histogram on %i Events'%(self.tag, len(df.columns))
        
        self._set_valstr(df)
        return self.plot_impact_hist(df,
                     title=title, xlab = 'raster value',

                     val_str=self.val_str, **kwargs)
        

    def plot_boxes(self, #plot boxplots of results
                     df=None, 
                      **kwargs): 

        if df is None:df=self.res_df

        title = '%s Raster Sample Boxplots on %i Events'%(self.tag, len(df.columns))

        self._set_valstr(df)
        
        return self.plot_impact_boxes(df,
                     title=title, xlab = 'hazard layer', ylab = 'raster value',
                     smry_method='mean',
                     val_str=self.val_str,   **kwargs)
        
    def _set_valstr(self, df):
        self.val_str= 'finv_fcnt=%i \nfinv_name=\'%s\' \nas_inun=%s \ngtype=%s \ndate=%s'%(
            len(df), self.finv_name, self.as_inun, self.gtype, self.today_str)
        
        if not 'Point' in self.gtype:
            self.val_str = self.val_str + '\npsmp_stat=%s'%self.psmp_stat
        
    

    

            
        