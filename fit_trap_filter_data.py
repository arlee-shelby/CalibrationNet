from calibrationnet.db import get_session
from sqlalchemy import select
from calibrationnet.models import RunPixel
from calibrationnet.models import TrapFilterOutput
import numpy as np
import matplotlib.pyplot as plt
import fit_functions as f
from lmfit import Parameters
import pylab as py
from importlib import reload
from scipy.signal import find_peaks

with get_session() as session:
    runpixel = session.execute(select(RunPixel).where(RunPixel.run_number==8622,RunPixel.
                                                      pixel_number==60)).scalars().all()
    runpixel_id = runpixel[0].id
    raw_energy = session.execute(select(TrapFilterOutput.energies).
                                 where(TrapFilterOutput.run_pixel_id==runpixel_id)).scalars().all()

data = raw_energy[0]
lower_bound = 1200
upper_bound = 3200
peak_finder_parameters = (5,None,20,15,1,None,0.5,None)
number_peaks = 6
initial_peak_width_guess = {'sig1':3,'sig2':3,'sig3':3,'sig4':5,'sig5':5,'sig6':5}

results = f.get_fit(data,lower_bound, upper_bound,peak_finder_parameters,number_peaks,
                    initial_peak_width_guess,plot=False)

fit_parameter_value_names = list(results.params.keys())
##for every parameter in the fit add this to the spectrum fits table

for i in range(len(fit_parameter_value_names)):
    #this gets the parameter value
    v = results.params[fit_parameter_value_names[i]].value
    
    #this gets the parameter erorr
    e = results.params[fit_parameter_value_names[i]].stderr

    
#Also add other fit information that is important

#this gets the fit chi2
chi2 = results.chisqr

#this gets the fit reduced chi2
reduced_chi2 = results.redchi

#this gets number of degrees of greedom of fit
number_of_degrees_of_freedom = results.nfree


#How do we store covarient_matrix?
covariant_matrix = results.covar

#example of one paramter correlation dictionary, how do we store this as well?
#note that if you call another parameter's correlation it will repeat the correlation with slope and we don't want 
#to repeat entries. 
results.params['slope'].correl


lower_bound = 20
upper_bound = 180
peak_finder_parameters = (5,None,20,15,1,None,0.5,None)
number_peaks = 2
initial_peak_width_guess = {'sig1':3,'sig2':3,'sig3':5}

results2 = f.get_fit(data,lower_bound, upper_bound,peak_finder_parameters,number_peaks,
                     initial_peak_width_guess,plot=False)

##do same thing as above to store fit results for lower energy peaks