import h5py
import pandas as pd
import sys
import os
import numpy as np
import matplotlib.pyplot as plt

sys.path.append('/storage/home/hcoda1/4/ashelby8/Manitoba/pyNab/src/')
import nabPy as Nab

# sys.path.append('/storage/home/hcoda1/4/ashelby8/scratch/NabCalibration/')
# import DataAnalysis.bi_fit_functions_copy as bi_fit2
import functions_peak_fitting as bi_fit2


def get_pulser_energy(file, risetime, flat_top, fall_time):
    run = h5py.File(file)
    waves = Nab.da.from_array(run['waveforms/pulsers/waveforms'])
    headers = Nab.da.from_array(run['waveforms/pulsers/headers'])
    pixel_map = run['Parameters/BoardChannelToPixelMap'][()]
    pixels = pixel_map[headers.compute()['bc']][:,1]
    
    energy, time = Nab.bf.applyTrapFilter(waves,risetime,flat_top,fall_time,useGPU = False)
    df = pd.DataFrame(energy,pixels)
    return df

def get_singles_energy(file,risetime, flat_top, fall_time):
    run = h5py.File(file)
    waves = Nab.da.from_array(run['waveforms/singles/waveforms'])
    headers = Nab.da.from_array(run['waveforms/singles/headers'])
    pixel_map = run['Parameters/BoardChannelToPixelMap'][()]
    pixels = pixel_map[headers.compute()['bc']][:,1]
    
    energy, time = Nab.bf.applyTrapFilter(waves,risetime,flat_top,fall_time,useGPU = False)
    df = pd.DataFrame(energy,pixels)
    return df

def get_runs_in_folder(directory,delimiter="_"):
    # A set will only store unique prefix values
    unique_prefixes = set()
    
    # Iterate through all files in the given directory
    for filename in os.listdir(directory):
        # Ensure we are only looking at files and they contain the delimiter
        if os.path.isfile(os.path.join(directory, filename)) and delimiter in filename:
            # Extract the part before the first delimiter
            prefix = filename.split(delimiter)[0]
            unique_prefixes.add(prefix)
            
    return len(unique_prefixes), unique_prefixes

def get_file_data(run_number,risetime,flat_top):
    directory = '/storage/home/hcoda1/4/ashelby8/scratch/FilterOutput/'
    file = directory+'Run%d/Singles/filter_output_rt%d_ft%d.csv'%(run_number,risetime,flat_top)
    data = pd.read_csv(file,index_col='pixel')
    return data

def get_all_pixel_histograms(data,detector):
    histograms = {}
    j = 0
    for i in range(1,128):
        try:
            if detector=='LDET':
                i = i+1000

            energy,bins = np.histogram(data.loc[i].values,bins=np.arange(0,4500))
            if max(energy[30:])<200 or np.argmax(energy[30:])<500:
                continue
            else:
                histograms['%d'%i] = energy
        except Exception as e:
            continue
    return histograms

def get_histogram_energy(run_number,risetime,flat_top,detector='UDET'):
    data = get_file_data(run_number,risetime,flat_top)
    histograms = get_all_pixel_histograms(data,detector)
    return histograms

def fit_UDET_data(run_number):
    sigmas = {}
    sigmas_error = {}
    fit_results = {}
    bins = np.arange(0,4500)
    for j in range(10,70,10):
        for i in range(50,1350,50):
            histograms = get_histogram_energy(run_number,i,j)
            pixels = list(histograms.keys())
            peak_finder_props = (10,None,20,35,10,None,0.5,None)
            for k in range(len(pixels)):
                if any(value==pixels[k] for value in sigmas.keys()):
                    if any(value==j for value in sigmas[pixels[k]].keys()):
                        pass
                    else:
                        sigmas[pixels[k]][j] = {}
                        sigmas_error[pixels[k]][j] = {}
                        fit_results[pixels[k]][j] = {}
                else:
                    sigmas[pixels[k]] = {}
                    sigmas[pixels[k]][j] = {}

                    sigmas_error[pixels[k]] = {}
                    sigmas_error[pixels[k]][j] = {}

                    fit_results[pixels[k]] = {}
                    fit_results[pixels[k]][j] = {}

                try:
                    sigs2_df = {'sig1':5}
                    r = bi_fit2.get_UDETbi_fit_long(run_number,histograms,bins,pixels[k],2700,3050,1,peak_finder_props,sigs2_df,plot=False)
                    if r['sig1']['error']>5 or r['red chi2']>10:
                            raise TypeError("Forced error 1, error too large")

                except Exception as e:
                    try:
                        sigs2_df = {'sig1':3}
                        r = bi_fit2.get_UDETbi_fit_long(run_number,histograms,bins,pixels[k],2700,3050,1,peak_finder_props,sigs2_df,plot=False)
                        if r['sig1']['error']>5 or r['red chi2']>10:
                            raise TypeError("Forced error 2, error too large")

                    except Exception as e:
                        print('risetime:%d, flat_top:%d, pixle:%s'%(i,j,pixels[k]),e)
                        continue
                        
                sigmas[pixels[k]][j][i] = r['sig1']['value']
                sigmas_error[pixels[k]][j][i] = r['sig1']['error']
                fit_results[pixels[k]][j][i] = r

    return sigmas, sigmas_error, fit_results


def fit_LDET_data(run_number):
    LDETsigmas = {}
    LDETsigmas_error = {}
    LDETfit_results = {}
    bins = np.arange(0,4500)
    for j in range(10,70,10):
        for i in range(50,1350,50):
            histograms = get_histogram_energy(run_number,i,j,detector='LDET')
            pixels = list(histograms.keys())
            peak_finder_props = (10,None,20,35,10,None,0.5,None)
            for k in range(len(pixels)):
                if any(value==pixels[k] for value in LDETsigmas.keys()):
                    if any(value==j for value in LDETsigmas[pixels[k]].keys()):
                        pass
                    else:
                        LDETsigmas[pixels[k]][j] = {}
                        LDETsigmas_error[pixels[k]][j] = {}
                        LDETfit_results[pixels[k]][j] = {}
                else:
                    LDETsigmas[pixels[k]] = {}
                    LDETsigmas[pixels[k]][j] = {}
                    LDETsigmas_error[pixels[k]] = {}
                    LDETsigmas_error[pixels[k]][j] = {}
                    LDETfit_results[pixels[k]] = {}
                    LDETfit_results[pixels[k]][j] = {}

                try:
                    sigs2_df = {'sig1':30}
                    r = bi_fit2.get_UDETbi_fit_long(run_number,histograms,bins,pixels[k],2700,3050,1,peak_finder_props,sigs2_df,plot=False)
                    if r['sig1']['error']>5 or r['red chi2']>10:
                        raise TypeError("Forced error 1, error too large")

                except Exception as e:
                    try:
                        sigs2_df = {'sig1':20}
                        r = bi_fit2.get_UDETbi_fit_long(run_number,histograms,bins,pixels[k],2700,3050,1,peak_finder_props,sigs2_df,plot=False)
                        if r['sig1']['error']>5 or r['red chi2']>10:
                            raise TypeError("Forced error 2, error too large")

                    except Exception as e:
                        try:
                            sigs2_df = {'sig1':10}
                            r = bi_fit2.get_UDETbi_fit_long(run_number,histograms,bins,pixels[k],2700,3050,1,peak_finder_props,sigs2_df,plot=False)
                            if r['sig1']['error']>5 or r['red chi2']>10:
                                raise TypeError("Forced error 3, error too large")

                        except Exception as e:
                            print('risetime:%d, flat_top:%d, pixle:%s'%(i,j,pixels[k]),e)
                            continue

                LDETsigmas[pixels[k]][j][i] = r['sig1']['value']
                LDETsigmas_error[pixels[k]][j][i] = r['sig1']['error']
                LDETfit_results[pixels[k]][j][i] = r
    return LDETsigmas, LDETsigmas_error, LDETfit_results