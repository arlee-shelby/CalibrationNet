import numpy as np
from lmfit import Parameters
import pylab as py
from scipy import special
from scipy.signal import find_peaks
from DataAnalysis.basic_functions import gauss
from DataAnalysis.basic_functions import lower_exp
from DataAnalysis.basic_functions import upper_exp
from DataAnalysis.basic_functions import step_function
from DataAnalysis.basic_functions import background
from DataAnalysis.basic_functions import get_hist_data_uncert
from DataAnalysis.basic_functions import get_fit

def lower_exp2(x,x0,beta,sig,amp):
    return (amp)*np.exp((sig**2/(2*beta**2))+((x-x0)/beta))*(1-special.erf((x-x0)/(np.sqrt(2)*sig) + sig/(np.sqrt(2)*beta)))
#     return (amp*n)*np.exp((sig**2/(2*beta**2))+((x-x0)/beta))*(1-special.erf((x-x0)/(np.sqrt(2)*sig) + sig/(np.sqrt(2)*beta)))

def step_function2(x,amp,x0,sig):
    return (amp)*(1-special.erf((x-x0)/(np.sqrt(2)*sig)))

def bi_model(params,x):

    num_peaks = params['num_peaks'].value

    # sig_ratio = params['sig_ratio'].value
    # amp_ratio = params['amp_ratio'].value

    intercept = params['intercept'].value
    slope = params['slope'].value
    beta = params['beta'].value
    
    peak_func = 0
    for i in range(int(num_peaks)):
        i+=1
        amp = params['amp%d'%i].value
        cen = params['cen%d'%i].value
        sig = params['sig%d'%i].value
#         beta = params['beta%d'%i].value
        n= params['n%d'%i].value
        h = params['h%d'%i]

        z = (x-cen)/sig

        peak = gauss(z,amp*(1-n)) + lower_exp2(x,cen,beta,sig,amp*n) + step_function2(x,amp*h,cen,sig)
        peak_func += peak
    
    linear_background = background(x, slope, intercept)

    return peak_func + linear_background

def bi_residual(params, x, y, alpha):
    model = bi_model(params, x)
    return (model - y) / alpha

def add_bi_params(params,initial_peak_props,initial_parameter_values=None):

    if initial_parameter_values==None:
        # params.add('sig_ratio',value=0.05,min=0)
        # params.add('amp_ratio',value=0.6,min=0, max=1)

        params.add('slope',value=-1e-3) #original
        params.add('intercept',value=0)
        params.add('beta',value=10)
        for i in range(params['num_peaks'].value):
            i+=1
            params.add('amp%d'%i,value=initial_peak_props['amp%d'%i],min=0)
            params.add('cen%d'%i,value=initial_peak_props['cen%d'%i],min=0)
            params.add('sig%d'%i,value=initial_peak_props['sig%d'%i],min=0)
#             params.add('beta%d'%i,value=10)
            params.add('n%d'%i,value=0.6,min=0,max=1)
            params.add('h%d'%i,0.1,min=0,max=1)
            # params.add('step%d_ratio'%i, value=0.01,min=-1e-1, max=1)

    else:
        # params.add('sig_ratio',value=initial_parameter_values['sig_ratio'],min=0)
        # params.add('amp_ratio',value=initial_parameter_values['amp_ratio'],min=0, max=1)

        params.add('slope',value=initial_parameter_values['slope']) #original
        params.add('intercept',value=initial_parameter_values['intercept'])

        for i in range(params['num_peaks'].value):
            i+=1
            params.add('amp%d'%i,value=initial_peak_props['amp%d'%i],min=0)
            params.add('cen%d'%i,value=initial_peak_props['cen%d'%i],min=0)
            params.add('sig%d'%i,value=initial_peak_props['sig%d'%i],min=0)
            params.add('beta%d'%i,value=initial_parameter_values['beta%d'%i],min=0)
            params.add('n%d'%i,value=initial_parameter_values['n%d'%i],min=0,max=1)
            params.add('h%d'%i,value=initial_parameter_values['h%d'%i],min=0,max=1)
            # params.add('step%d_ratio'%i, value=initial_parameter_values['step%d_ratio'%i],min=-1e-1, max=1)

def get_UDETinitial_peak_props(xdat,ydat,peak_finder_props,num_peaks,initial_peak_sigmas):
    initial_peak_props = {}
    find_peaks.__defaults__ = peak_finder_props
    peaks, props = find_peaks(ydat)
    while len(peaks)>num_peaks:
        prop_list = list(peak_finder_props)
        prop_list[3] += 10

        peak_finder_props = tuple(prop_list)
        find_peaks.__defaults__ = peak_finder_props

        peaks, props = find_peaks(ydat)
    # print(peaks)
    for i in range(num_peaks):
        i += 1
        if len(peaks)==num_peaks:
            initial_peak_props['amp%d'%i] = props['peak_heights'][i-1]
            initial_peak_props['cen%d'%i] = xdat[peaks[i-1]]
            initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
        
        elif len(peaks)==num_peaks-1:
            if i!=num_peaks:
                initial_peak_props['amp%d'%i] = props['peak_heights'][i-1]
                initial_peak_props['cen%d'%i] = xdat[peaks[i-1]]
                initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
            else:
                initial_peak_props['amp%d'%i] = props['peak_heights'][-1]*0.5
                initial_peak_props['cen%d'%i] = xdat[peaks[-1]]+36
                initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
        else:
            if num_peaks==2:
                if i<num_peaks:
                    initial_peak_props['amp%d'%i] = max(ydat)
                    initial_peak_props['cen%d'%i] = (np.argmax(ydat)+xdat[0])
                    initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
                else:
                    initial_peak_props['amp%d'%i] = max(ydat)*0.5
                    initial_peak_props['cen%d'%i] = (np.argmax(ydat)+xdat[0])+36
                    initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
            if num_peaks==3:
                if i==1:
                    amp_scale = 1
                    cen_shift = 0
                if i==2:
                    amp_scale = 1/3
                    cen_shift = 216
                if i==3:
                    amp_scale = 1/6
                    cen_shift = 252
                initial_peak_props['amp%d'%i] = max(ydat)*amp_scale
                initial_peak_props['cen%d'%i] = (np.argmax(ydat)+xdat[0]) + cen_shift
                initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
    return initial_peak_props
    
def get_UDETbi_fit_long(run_number,data,bin_edges,pixel,low_region,up_region,num_peaks,peak_finder_props,initial_peak_sigmas,plot=False,initial_parameter_values=None):
    cnt = 0
    nrows,ncols=8,4
    fig = py.figure(figsize=(8*ncols,6*nrows))
    if not any(value==pixel for value in data.keys()):
        py.close(fig)
        print('pixel %s not found for run %d'%(pixel,run_number))
        return
    else:
        df = {}
        df['time'] = {}

        xdat = np.array(bin_edges[low_region:up_region])
        ydat = np.array(data[pixel][low_region:up_region])

        alpha = get_hist_data_uncert(ydat)

        try:
            initial_peak_props = get_UDETinitial_peak_props(xdat,ydat,peak_finder_props,num_peaks,initial_peak_sigmas)
#             print(initial_peak_props)
        except Exception as e:
            print('could not get initial peak props for run: %d, pixel: %s'%(run_number,pixel))
            return
        
        params = Parameters()
        params.add('num_peaks', value=num_peaks,vary=False)
        try:
            if initial_parameter_values==None:
                add_bi_params(params,initial_peak_props)
            else:
                add_bi_params(params,initial_peak_props,initial_parameter_values)
        except Exception as e:
            print('could not add params, run: %d, pixel: %s, low reg: %d'%(run_number,pixel,low_region),e)
            return
        
        model=bi_model
        residual_model = bi_residual

        try:
#             print(params,len(params))
            bestfit, result = get_fit(model,residual_model, params, xdat, ydat,alpha)

            if result.errorbars:
                df['chi2'] = result.chisqr
                df['red chi2'] = result.redchi

                if plot:
                    cnt+=1
                    ax=py.subplot(nrows,ncols,cnt)
                    ax.step(xdat, ydat, alpha=0.5)
                    ax.plot(xdat, bestfit,label='Run %d, Pixel %s red chi: %.2f'%(run_number,pixel,result.redchi))
                    ax.set_ylabel('Counts')
                    ax.set_xlabel('Energy (ADC)')
                    ax.legend()
                    py.show()
                else:
                    py.close(fig)

                for key in result.params.keys():
                    df['%s'%key] = {}
                    df['%s'%key] = {}
                    df['%s'%key]['value'] = result.params['%s'%key].value
                    df['%s'%key]['error'] = result.params['%s'%key].stderr
            else:
                print("no errors found for run: %d, pixel: %s, region "%(run_number,pixel))
                py.close(fig)
                return
        except Exception as e:
            
            print("failed Bi fit for run: %d, pixel: %s, region"%(run_number,pixel))
            py.close(fig)
            return
        return df
    
def get_UDETbi_fit_results(energy, bin_edges, pixel, run_numbers,low_region=40,plot=False,detector_type='UDET'):
    # sigs0_df = {'sig1':6,'sig2':6}
    sigs0_df = {'sig1':7,'sig2':7} #original
    # sigs1_df = {'sig1':5,'sig2':7,'sig3':7} #1017, 1029 (run 8717)
    # sigs1_df = {'sig1':5,'sig2':6,'sig3':6} #original, and 1017 (run 8829)
    sigs1_df = {'sig1':5,'sig2':6,'sig3':6}
    sigs2_df = {'sig1':5,'sig2':7,'sig3':7}


    if detector_type=='UDET':
        # peak_finder_props = (10,None,5,35,3,None,0.5,None) #original
        peak_finder_props = (10,None,1,35,3,None,0.5,None) # for 91
        # peak_finder_props = (10,None,5,25,1,None,0.5,None) #for 95
    if detector_type=='LDET':
        # peak_finder_props = (10,None,5,35,6,None,0.5,None)
        peak_finder_props = (10,None,5,35,6,None,0.5,None)
        if pixel=='1017' or pixel=='1021' or pixel=='1028' or pixel=='1032' or pixel=='1043' or pixel=='1054':
            low_region=100
        else:
            # low_region=100
            low_region=100
    results = {}

    for i in run_numbers:
        if not any(value==pixel for value in energy[i].keys()) or np.argmax(energy[i][pixel][low_region:])+low_region<60: #original <300
            # v = np.argmax(energy[i][pixel][low_region:])
            # print('here',v)
            pass
        
        else:
            r1,r2,r3 = None,None,None
            idx1,idx2,idx3 = None,None,None
            if detector_type=='LDET':
                mean = np.mean(energy[i][pixel][low_region:400])
                if mean<15:
                    print('LDET average too low, run %d, pixel %s'%(i, pixel))
                    pass
                else:
                    # print('here1')
                    r = get_UDETbi_fit_long(i,energy[i],bin_edges,pixel,low_region,400,2,peak_finder_props,sigs0_df,plot=plot)
            else:
                # print('here2')
                if pixel=='91':
                    r1=None
                    pass
                else:
                    r = get_UDETbi_fit_long(i,energy[i],bin_edges,pixel,low_region,400,2,peak_finder_props,sigs0_df,plot=plot)
            try:
                if detector_type=='UDET':
                    if r['amp1']['value']<15:
                        print('run: %d, pixel: %s too low amp, region 1'%(i, pixel))
                        pass
                    else:
                        r1 = r
                        idx1 = 0
                else:
                    # print('here3')
                    if r['amp1']['value']<15 or r['sig1']['value']<2 or r['sig2']['value']<2:
                        print('run: %d, pixel: %s too low amp, sig1 or sig2 region 1'%(i, pixel))
                        # print('here4')
                        pass
                    else:
                        # print('here5')
                        r1 = r
                        idx1 = 0
            except Exception as e:
                pass
            low_region2 = 1200
            high_region2 = 1900
            if detector_type=='LDET':
                if pixel=='1017' or pixel=='1021' or pixel=='1028' or pixel=='1032' or pixel=='1043' or pixel=='1054':
                    low_region2 = 800 #original
                    high_region2 = 1200 #original
                    # low_region2 = 350 #1017, 1032
                    # high_region2 = 700 #1017, 1032
                    # low_region2 = 300 #1021
                    # high_region2 = 550 #1021
            if detector_type=='UDET':
                if pixel=='9' or pixel=='91' or pixel=='95' or pixel=='96':
                    sigs1_df = {'sig1':1,'sig2':1,'sig3':1} #for 91
                    low_region2 = 500
                    high_region2 = 800
                    #900 original (but wasn't actually working)
                    low_region2 = 30*4 #for 91
                    high_region2 = 50*4 # for 91
            r = get_UDETbi_fit_long(i,energy[i],bin_edges,pixel,low_region2,high_region2,3,peak_finder_props,sigs1_df,plot=plot)
            try:
                # if r['amp1']['value']<15 or r['amp2']['value']<0 or r['amp3']['value']<0 or r['sig1']['value']<2 or r['sig2']['value']<2 or r['sig3']['value']<2:
                if r['amp1']['value']<15 or r['amp2']['value']<0:
                    print('run: %d, pixel: %s too low amp, region 2'%(i, pixel),r)
                    pass
                else:
                    r2 = r
                    idx2 = 1

            except Exception as e:
                pass
            
            low_region3 = 2700
            high_region3 = 3400
            if detector_type=='LDET':
                # peak_finder_props = (10,None,20,35,6,None,0.5,None)
                peak_finder_props = (10,None,20,35,10,None,0.5,None)
                if pixel=='1017' or pixel=='1021' or pixel=='1028' or pixel=='1032' or pixel=='1043' or pixel=='1054':
                    low_region3 = 1500 #original
                    high_region3 = 2500 #original
                    # low_region3 = 750 #1017, 1032
                    # # high_region3 = 1250 #1017
                    # high_region3 = 1250 #1032
                    # low_region3 = 650 #1017
                    # high_region3 = 950 #1017
            if detector_type=='UDET':
                if pixel=='9' or pixel=='91' or pixel=='95' or pixel=='96':
                    sigs2_df = {'sig1':1,'sig2':1,'sig3':1} #for 91
                    low_region3 = 1100
                    high_region3 = 1600
                    low_region3 = 70*4 #for 91
                    high_region3 = 90*4 #for 91
                    peak_finder_props = (10,None,1,35,1,None,0.5,None)
            r = get_UDETbi_fit_long(i,energy[i],bin_edges,pixel,low_region3,high_region3,3,peak_finder_props,sigs2_df,plot=plot)
            try:
                if r['amp1']['value']<15:
                    print('run: %d, pixel: %s too low amp, region 3'%(i, pixel))
                    pass
                else:
                    r3 = r
                    idx3 = 2
            except Exception as e:
                pass
            # print(r1,r2,r3)
            if r1!=None or r2!=None or r3!=None:
                results[i] = {}
                if r1!=None:
                    results[i][idx1] = r1
                if r2!=None:
                    results[i][idx2] = r2
                if r3!=None:
                    results[i][idx3] = r3
        r = None
    return results