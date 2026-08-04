import numpy as np
from lmfit import Minimizer, Parameters
import pylab as py
from scipy import special
from scipy.signal import find_peaks

def gaussian(z,p1):
    return p1*np.exp(-0.5*(z)**2)

def background(x,p7,p8):
    return p7*x+p8

def lower_exponential(x,x0,beta,sig,amp):
    return (amp)*np.exp((sig**2/(2*beta**2))+((x-x0)/beta))*(1-special.erf((x-x0)/(np.sqrt(2)*sig) + sig/(np.sqrt(2)*beta)))

def step_function(x,amp,x0,sig):
    return (amp)*(1-special.erf((x-x0)/(np.sqrt(2)*sig)))

def fit_model(params,x):

    num_peaks = params['num_peaks'].value
    intercept = params['intercept'].value
    slope = params['slope'].value
    beta = params['beta'].value
    
    peak_func = 0
    for i in range(int(num_peaks)):
        i+=1
        amp = params['amp%d'%i].value
        cen = params['cen%d'%i].value
        sig = params['sig%d'%i].value
        n= params['n%d'%i].value
        h = params['h%d'%i]

        z = (x-cen)/sig

        peak = gaussian(z,amp*(1-n)) + lower_exponential(x,cen,beta,sig,amp*n) + step_function(x,amp*h,cen,sig)
        peak_func += peak
    
    linear_background = background(x, slope, intercept)

    if 'threshold' in params and params['threshold'].value:
        threshold_z = x/params['threshold_sig'].value
        threshold_amp = params['threshold_amp'].value
        fit_func = peak_func + linear_background + gaussian(threshold_z,threshold_amp)

    else:
        fit_func = peak_func + linear_background

    return fit_func

def residual_function(params, x, y, alpha):
    model = fit_model(params, x)
    return (model - y) / alpha

def add_parameters(params,initial_peak_parameters,initial_parameter_values=None,threshold_params={}):

    if threshold_params!={}:
        params.add('threshold',value=True,vary=False)
        params.add('threshold_sig',value=threshold_params['sig'],min=0)
        params.add('threshold_amp',value=threshold_params['amp'],min=0)

    if initial_parameter_values==None:
        params.add('slope',value=-1e-3)
        params.add('intercept',value=0)
        params.add('beta',value=10)
        for i in range(params['num_peaks'].value):
            i+=1
            params.add('amp%d'%i,value=initial_peak_parameters['amp%d'%i],min=0)
            params.add('cen%d'%i,value=initial_peak_parameters['cen%d'%i],min=0)
            params.add('sig%d'%i,value=initial_peak_parameters['sig%d'%i],min=0)
            params.add('n%d'%i,value=0.6,min=0,max=1)
            params.add('h%d'%i,0.1,min=0,max=1)

    else:
        params.add('slope',value=initial_parameter_values['slope']) #original
        params.add('intercept',value=initial_parameter_values['intercept'])
        for i in range(params['num_peaks'].value):
            i+=1
            params.add('amp%d'%i,value=initial_peak_parameters['amp%d'%i],min=0)
            params.add('cen%d'%i,value=initial_peak_parameters['cen%d'%i],min=0)
            params.add('sig%d'%i,value=initial_peak_parameters['sig%d'%i],min=0)
            params.add('beta%d'%i,value=initial_parameter_values['beta%d'%i],min=0)
            params.add('n%d'%i,value=initial_parameter_values['n%d'%i],min=0,max=1)
            params.add('h%d'%i,value=initial_parameter_values['h%d'%i],min=0,max=1)

def get_initial_peak_parameters(xdat,ydat,peak_finder_props,num_peaks,initial_peak_sigmas):
    initial_peak_props = {}
    find_peaks.__defaults__ = peak_finder_props
    peaks, props = find_peaks(ydat)
    while len(peaks)>num_peaks:
        prop_list = list(peak_finder_props)
        prop_list[3] += 10

        peak_finder_props = tuple(prop_list)
        find_peaks.__defaults__ = peak_finder_props

        peaks, props = find_peaks(ydat)
    for i in range(num_peaks):
        i += 1
        if len(peaks)==num_peaks:
            initial_peak_props['amp%d'%i] = props['peak_heights'][i-1]
            initial_peak_props['cen%d'%i] = xdat[peaks[i-1]]
            initial_peak_props['sig%d'%i] = initial_peak_sigmas['sig%d'%i]
    return initial_peak_props

def get_histogram_data_uncertainty(counts):
    uncertainty = np.sqrt(counts)
    for k in range(len(uncertainty)):
        if uncertainty[k]<1:
            uncertainty[k]=1
    return uncertainty

def do_fit(params,xdata,ydata,y_uncertainty):

    mini = Minimizer(residual_function, params, fcn_args=(xdata, ydata, y_uncertainty),scale_covar=False)
    fit_result = mini.minimize()
    evaluated_fit_result = fit_model(fit_result.params, xdata)
    return evaluated_fit_result, fit_result

def get_fit(data,lower_bound, upper_bound,peak_finder_parameters,number_peaks,initial_peak_width_guess,plot=False,axis=None,threshold_params={}):
    histogram = np.histogram(data,bins = np.arange(0,4500))
    ydata = histogram[0]
    xdata = histogram[1]
    yuncertainty = get_histogram_data_uncertainty(ydata)

    fit_xdata = xdata[lower_bound:upper_bound]
    fit_ydata = ydata[lower_bound:upper_bound]
    fit_yuncertainty = yuncertainty[lower_bound:upper_bound]

    initial_peak_parameters = get_initial_peak_parameters(fit_xdata,fit_ydata,peak_finder_parameters,number_peaks,initial_peak_width_guess)

    params = Parameters()
    params.add('num_peaks', value=number_peaks,vary=False)
    add_parameters(params,initial_peak_parameters,threshold_params=threshold_params)

    evaluated_fit_result,fit_result = do_fit(params,fit_xdata,fit_ydata,fit_yuncertainty)

    if plot and axis!=None:
        axis.plot(fit_xdata,fit_ydata)
        axis.plot(fit_xdata,evaluated_fit_result,label=r'Reduced $\chi$: %.2f'%fit_result.redchi)
        axis.set_ylabel('Counts')
        axis.set_xlabel('Energy (ADC)')
        axis.legend()

    return fit_result





