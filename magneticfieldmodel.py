import os
import sys
import numpy as np
import pyfftw

from astropy.convolution import convolve, Gaussian2DKernel
from astropy.cosmology import FlatLambdaCDM
import astropy.units as u
from astropy.constants import c as speed_of_light

from scipy.optimize import curve_fit

import argparse
import matplotlib.pyplot as plt

import time

import multiprocessing as mp
import gc 

import psutil
import pyFC
from functools import partial
from scipy.interpolate import interp1d

cosmo = FlatLambdaCDM(H0=70, Om0=0.3)



### TODO
# import xray_surface_brightness
# import SZ_profile



starttime = time.time()
print ("\n\nScript is starting..")

pid = os.getpid()
python_process = psutil.Process(pid)


"""
# Simulate a magnetic field following Murgia+2004; https://arxiv.org/abs/astro-ph/0406225
# approach is first mentioned in Tribble+1991.


### ALICE VERSION: USE SYMMETRY. Try to also use smaller dtypes. 

"""







def print_field(field,i,j,k):
    """ Convenience function """
    print(field[i,j,k])
    print(field[-i,-j,-k])

def model_xi(k, xi, N, Lambda_max=None, indices=True):
    """
    Evaluate a given powerlaw Pk ~ k^-xi 
    With possible maximum spatial scale Lambda_max given in kpc

    The maximum scale is defined as the magnetic field reversal scale,
    see footnote in Murgia+2004. In this way, Lambda = 0.5* 2*np.pi/k 
    Thus the smallest possible k mode (k=1) always corresponds to Lambda=(N*pixsize)/2
    e.g., Lambda_max = 512 kpc for N=1024 and p=1
    Thus the next k mode (k=2) corresponds to 256 kpc and k=2 to 128 kpc etc..

    indices -- boolean -- whether 'k' (the 'k-modes') are given as indices or as values
    """

    if Lambda_max is None: # scale invariant. Easy.
        return k**-xi
    else:
        result = k**-xi

        # The wave mode that corresponds to the given Lambda_max in kpc
        #### Following Murgia definition that Lambda is the half-wavelength = 0.5*(2pi/k)
        kmax = np.pi/Lambda_max  

        # The index of the wave mode that corresponds to the given Lambda max in kpc
        k_index_max = (N*pixsize/2) / Lambda_max

        # Because the Gaussian_random_field function uses indices, indices
        # are given to this function, so we should mask on index length
        if indices:
            result[k<k_index_max] = 0

        else: # Mask all k modes that are smaller than kmax, corresponds to larger than Lambda_max
            result[k<kmax] = 0

        return result

def kvector(N,ndim, pixsize=1):
    """
    Generate ( N(xN)xN//2+1) ndim matrix of k vector values
    Since we need to do the IFFT of k cross A
    we also need this kvector array
    """
    dk = ftype(2*np.pi/N/pixsize)
    # Frequency terms, positive frequencies up unto half of the array
    # Nyquist frequency at position N//2, then negative frequencies up to -1
    ks = np.array(np.concatenate([np.arange(0, N//2+1),np.arange(-N//2+1,0)]),dtype=ftype)
    ks *= dk
                
    # My implementation of the c_field has a different definition
    # for the x axis than numpy, thus swap y and x from np.meshgrid
    if ndim == 2:
        # every particle has a 2D position
        kvector = np.zeros((N,N//2+1,ndim),dtype=ftype)
        # simply replaces more of the same for loops
        ky, kx = np.meshgrid(ks,ks) # construct a grid
        kvector[:,:,0] = kx[:,:N//2+1]
        kvector[:,:,1] = ky[:,:N//2+1]
    elif ndim == 3:
        # every particle has a 3D position. Only need half of the cube?
        kvector = np.zeros((N,N,N//2+1,ndim),dtype=ftype)
        ky, kx, kz = np.meshgrid(ks,ks,ks)
        kvector[:,:,:,0] = kx[:,:,:N//2+1]
        kvector[:,:,:,1] = ky[:,:,:N//2+1]
        kvector[:,:,:,2] = kz[:,:,:N//2+1]

    return kvector

def kvector_lengthonly(N):
    """
    Get the normalised length of the fft indices in 3D

    Only half of the cube is generated. Other half is redundant
    """

    kxkykz = np.zeros((N,N,N//2+1,3),dtype=ftype)
    indices = fftIndgen(N)
    ky, kx, kz = np.meshgrid(indices,indices,indices)
    kxkykz[:,:,:,0] = kx[:,:,:N//2+1]
    kxkykz[:,:,:,1] = ky[:,:,:N//2+1]
    kxkykz[:,:,:,2] = kz[:,:,:N//2+1]
    # Power spectrum only depends on the length
    k_length = np.linalg.norm(kxkykz,axis=-1)

    return k_length

def xvector(N,ndim,pixsize=1.0, subcube=False):
    """
    Generate NxN(xN)xndim matrix of x vector values
    
    This is simply a vector that goes from -31 to 32 because the real space has
    no origin before I set the origin. So xvec[31,N//2-1,N//2-1] is the origin

    if subcube -- Because its symmetric, only need a small part of cube (only positive quadrant)
    """
    xs = np.arange(-N//2+1,N//2+1,dtype=ftype)*pixsize # aranged vector of x positions
                
    # My implementation of the field has a different definition
    # for the x axis than numpy, thus swap y and x from np.meshgrid
    if ndim == 2:
        # every particle has a 2D position
        xvector = np.zeros((N,N,ndim),dtype=ftype)
        # simply replaces more of the same for loops
        y, x = np.meshgrid(xs,xs) # construct a grid
        xvector[:,:,0] = x
        xvector[:,:,1] = y
    elif ndim == 3:

        if not subcube:
            # every particle has a 3D position
            xvector = np.zeros((N,N,N,ndim),dtype=ftype)
            y, x, z = np.meshgrid(xs,xs,xs)
            xvector[:,:,:,0] = x
            xvector[:,:,:,1] = y
            xvector[:,:,:,2] = z

        else:
            # Use radial symmetry to only get  1/N^ndim of the cube
            # Only implemented for ndim==3, so 1/8th of the cube
            xvector = np.zeros((N//2+1,N//2+1,N//2+1,ndim),dtype=ftype)
            y, x, z = np.meshgrid(xs,xs,xs)
            xvector[:,:,:,0] = x[N//2-1:,N//2-1:,N//2-1:] # start from 0, omit negative part
            xvector[:,:,:,1] = y[N//2-1:,N//2-1:,N//2-1:]
            xvector[:,:,:,2] = z[N//2-1:,N//2-1:,N//2-1:]

            # e.g. for N=6 
            # x now contains [0,1,2,3]
            # instead of [-2,1,0,1,2,3]

    return xvector

def xvector_length(N, ndim, pixsize=1.0, subcube=False):
    """
    Call the function above and then compute only the length
    """
    # Now runs from -31 to +32 which is 64 values. Or 0 to +32 when subcube=True
    xvec = xvector(N, ndim, pixsize, subcube)
    # The norm of the position vector
    xvec_length = np.linalg.norm(xvec,axis=-1)
    return xvec_length

def cube_from_subcube(subcube, cubeshape):
    """
    Using radial symmetry to fill in the rest of the cube 

    if cubeshape is N then we assume (N,N,N), then subcube should be of shape (N//2+1,N//2+1,N//2+1)
    assuming that the symmetry (0) axis is the first index and the final index doesnt have to be flipped
    (i.e. also assuming that N == even)
    """
    N = cubeshape
    cube = np.zeros((N,N,N),dtype=ftype)

    ### Fill 8 subcubes by flipping across the negative axis
    # all 'negative' axis directions
    cube[:N//2-1,:N//2-1,:N//2-1] = np.flip(subcube[1:N//2,1:N//2,1:N//2])
    # 'positive' x direction, negative others
    cube[N//2-1:,:N//2-1,:N//2-1] = np.flip(subcube[0:,1:N//2,1:N//2],axis=(1,2))
    # 'positive' y direction, negative others
    cube[:N//2-1,N//2-1:,:N//2-1] = np.flip(subcube[1:N//2,0:,1:N//2],axis=(0,2))
    # 'positive' z direction, negative others
    cube[:N//2-1,:N//2-1,N//2-1:] = np.flip(subcube[1:N//2,1:N//2,0:],axis=(0,1))
    # positive x, positive y, negative z
    cube[N//2-1:,N//2-1:,:N//2-1] = np.flip(subcube[0:,0:,1:N//2],axis=(2))
    # positive x, negative y, positive z
    cube[N//2-1:,:N//2-1,N//2-1:] = np.flip(subcube[0:,1:N//2,0:],axis=(1))
    # negative x, positive y, positive z
    cube[:N//2-1,N//2-1:,N//2-1:] = np.flip(subcube[1:N//2,0:,0:],axis=(0))
    # all positive
    cube[N//2-1:,N//2-1:,N//2-1:] = subcube[0:,0:,0:]

    return cube

def radial_profile(data, center):
    """
    Calculate radial profile of array 'data', given the center 'center'
    """
    y, x = np.indices((data.shape))
    r = np.sqrt((x - center[0])**2 + (y - center[1])**2)
    r = r.astype(int)

    tbin = np.bincount(r.ravel(), data.ravel())
    nr = np.bincount(r.ravel())
    radialprofile = tbin / nr
    radius = np.arange(0,np.max(r)+1) # in pixels. This is how np.bincount bins.

    return radius, radialprofile 

def A_k_array(N, model, randgauss, counter=0, pixsize=1):
    """
    Generate NxNxN matrix of A_k values at every kx,ky_kz combination
    A_k only depends on the modulus of the k vector. 
    This is implicitly used in the function that generates the 2D field
    but is not as simple to implement in the 3D field implementation.
    Therefore we use this function.
    
    N         -- int: size of the field. ASSUMES EVEN NUMBER
    model     -- Power spectrum model function of k
    randgauss -- N**3 standard normal numbers for quick construction
    counter   -- which random number to start at
    """
    dk = 2*np.pi/N/pixsize
    # Frequency terms, positive frequencies up unto half of the array
    # Nyquist frequency at position N//2, then negative frequencies up to -1
    ks = np.array(np.concatenate([np.arange(0, N//2+1),np.arange(-N//2+1,0)]),dtype='float')
    ks *= dk

    # every particle has a 3D position
    # kvector = np.zeros((N,N,N,3))
    ky, kx, kz = np.meshgrid(ks,ks,ks)
    # Modulus, (64,64,64) array
    k = np.sqrt(kx**2 + ky**2 + kz**2)
    # Calculate the variance, set k[0,0,0] to 1 to prevent division by 0 
    k[0,0,0] = 1
    # Standard deviation as function of k.
    # Note the /2 of the variance, because most points will be complex conjugate
    # which doubles the variance
    std = np.sqrt(model(k)/2)
    ##### Fix the points that do not have double variance, they are their own complex conjugate
    # No contribution from point 0,0,0
    std[0,0,0] = 0 
    # The symmetric points are their own complex conjugate. Multiply variance by 2 again
    std[0, 0, N//2] = 4*std[0,0,N//2]
    std[0, N//2, 0] = 4*std[0,N//2,0]
    std[N//2, 0, 0] = 4*std[N//2, 0, 0]
    std[0, N//2, N//2] = 4*std[0,N//2,N//2]
    std[N//2, N//2, 0] = 4*std[N//2,N//2,0]
    std[N//2, 0, N//2] = 4*std[N//2, 0, N//2]
    std[N//2, N//2, N//2] = 4*std[N//2, N//2, N//2]

    # Multiply random numbers with the std to get a_k and b_k
    a_k = std*randgauss[counter:std.size+counter].reshape(std.shape)
    b_k = std*randgauss[counter+std.size:2*std.size+counter].reshape(std.shape)
    
    # Random complex number is simply a_k + i b_k 
    A_k = (a_k + 1j*b_k) # (64,64,64) 
    return A_k

def field3D(N, model, randgauss, counter=0, pixsize=1):
    """
    Generate the Fourier space of a real density field with mean 0
    that follows a given power spectrum model.
    The density field is generated in 3D
    
    N         -- int: size of the field
    model     -- Power spectrum model function of k
    randgauss -- N**3 standard normal numbers for quick construction
    counter   -- which random number to start at
    """
    
    fftfield = np.zeros((N,N,N),dtype='complex')
    # All random numbers we will ever need
    A_k = A_k_array(N, model, randgauss, counter, pixsize)
    # One step in k
    dk = 2*np.pi/N/pixsize
    # The fourier frequencies are different for (un)even N
    Neven = N%2 # add one to loops if N is uneven
    
    # Loop over all kz modes
    for z in range(0,N):
        # Loop over all kx modes
        for i in range(0,N): 
            # start at j=1 because we generate the kx's and kz's on the 
            # ky-axis seperately. Additionally, only generate the 
            # half of the fourier cube (ky>0)
            for j in range(1,N//2+Neven):
                # Use earlier computed c_k values
                fftfield[i,j,z] = A_k[i,j,z]
                
    if Neven == 0:
        # We have an even amount of N, so do not forget the j = N//2
        # plane. It's conjugate symmetry is special because -N//2 = N//2
        # Similarly, the j=0 plane is also conjugate symmetric in the y-axis
        
        
        # We can generate half of this plane, since it's symmetric in the y axis
        # and then impose symmetry on the plane itself.
        # This is the equivalent to generating a 2D plane density field
        for z in range(0,N): # loop over all kz modes
            for i in range(1,N//2): # Start at i=1 because we generate the kx's 
                                    # on the kx=0 axis seperately. 
                fftfield[i,N//2,z] = A_k[i,N//2,z]
                # Complex conjugate
                fftfield[-i,N//2,-z] = fftfield[i,N//2,z].real - 1j*(
                                    fftfield[i,N//2,z].imag)
                
                fftfield[i,0,z] = A_k[i,0,z]
                # Complex conjugate
                fftfield[-i,0,-z] = fftfield[i,0,z].real - 1j*(
                                    fftfield[i,0,z].imag)
                
                
        for z in range(0,N//2):
            # Don't forget the x=N//2 column, which we can generate half for
    
            fftfield[N//2,N//2,z] = A_k[N//2,N//2,z]
            # The other half is complex conjugate
            fftfield[N//2,N//2,-z] = fftfield[N//2,N//2,z].real - 1j*fftfield[N//2,N//2,z].imag
        
            # And the x=0 column, which we can generate half for. 
            fftfield[0,N//2,z] = A_k[0,N//2,z]
            # The other half is complex conjugate
            fftfield[0,N//2,-z] = fftfield[0,N//2,z].real - 1j*fftfield[0,N//2,z].imag
            SYMMETRY
            # SAME FOR WHEN y=0, also do the x=N//2 column  
            fftfield[N//2,0,z] = A_k[N//2,0,z]
            # The other half is complex conjugate
            fftfield[N//2,0,-z] = fftfield[N//2,0,z].real - 1j*fftfield[N//2,0,z].imag
        
            # And the x=0 column, which we can generate half for. 
            fftfield[0,0,z] = A_k[0,0,z]
            # The other half is complex conjugate
            fftfield[0,0,-z] = fftfield[0,0,z].real - 1j*fftfield[0,0,z].imag            
        

        # Now some numbers are their own complex conjugate.
        # i.e., they are real.
        # Their variance has already been doubled in the A_k_array function! 
        fftfield[0, 0, N//2] = A_k[0,0,N//2].real
        fftfield[0, N//2, 0] = A_k[0,N//2,0].real
        fftfield[N//2, 0, 0] = A_k[N//2, 0, 0].real
        
        fftfield[0, N//2, N//2] = A_k[0,N//2,N//2].real
        fftfield[N//2, N//2, 0] = A_k[N//2,N//2,0].real
        fftfield[N//2, 0, N//2] = A_k[N//2, 0, N//2].real
        
        fftfield[N//2, N//2, N//2] = A_k[N//2, N//2, N//2].real
        
    # Finally generate all modes with ky>0 (above j>N//2) by conjugating
    # all modes with ky < 0 
    for z in range(0,N):
        for i in range(0,N):
            for j in range(N//2,N):
                fftfield[i,j,z] = fftfield[-i,-j,-z].real - 1j*fftfield[-i,-j,-z].imag
    
    # Don't forget that the [0,0] component of the field has to be 0
    fftfield[0,0,0] = 0 + 1j*0   
    
    return fftfield

def fftIndgen(n):
    a = list(range(0, n//2+1))
    b = list(range(1, n//2))
    b.reverse()
    b = [-i for i in b]
    return a + b

def gaussian_random_field3D(N, Pk, k_length=None):
    """
    Adapted from http://andrewwalker.github.io/statefultransitions/post/gaussian-fields/

    Is actually nicely explained by https://garrettgoon.com/gaussian-fields/
    """
    if int(sys.version[0]) >= 3:
        # For some reason on ALICE we need this line even with py3
        # noise = pyfftw.interfaces.scipy_fftpack.fftn(np.random.normal(size = (N,N,N) ))
        # For some reason on ALICE this line below doesnt work
        # noise = pyfftw.interfaces.scipy_fft.fftn(np.random.normal(size = (N,N,N) ))

        ## Use fft for real values. Try to avoid copying to save memory
        run_fftw = pyfftw.builders.rfftn(np.random.normal(size = (N,N,N)).astype(ftype)
            , auto_contiguous=False, auto_align_input=False, avoid_copy=True,threads=48)
        noise = run_fftw()
    else:
        print("WARNING, USING PYTHON2. Recommended to use python3")
        noise = pyfftw.interfaces.scipy_fftpack.fftn(np.random.normal(size = (N,N,N) ).astype(ftype))

    if k_length is None:
        print ("WARNING. When calling gaussian_random_field multiple times. Recommended to give k_length")
        k_length = kvector_lengthonly(N)

    # amplitude = np.zeros((N,N,N))
    amplitude = np.sqrt(Pk(k_length))
    amplitude[0,0,0] = 0 # assume k=0 is on 0,0,0
    
    field = noise * amplitude
    return field

# Redefining the electron density model to follow the mean n_e profile

def ne_mean(r, r500):
        # Using the mean ne_profile
        r_mean  = np.load('mean_ne_profile.npy')[0] * r500
        mean_dens = np.load('mean_ne_profile.npy')[1]
        f = interp1d(r_mean, np.log10(mean_dens), kind='cubic', fill_value='extrapolate')
        return np.power(10,f(r))

def gen_ne_fluct(xi, Lambda_max=None, indices=True, Lambda_min=None, mu = 1, s = 0.2, mean = True, r500 = 925):
    '''
    The maximum scale is defined as the magnetic field reversal scale,
    see footnote in Murgia+2004. In this way, Lambda = 0.5* 2*np.pi/k 
    Thus the smallest possible k mode (k=1) always corresponds to Lambda=(N*pixsize)/2
    e.g., Lambda_max = 512 kpc for N=1024 and p=1
    Thus the next k mode (k=2) corresponds to 256 kpc and k=2 to 128 kpc etc..
    Parameters
    ----------
    indices -- boolean -- whether 'k' (the 'k-modes') are given as indices or as values

    mu -- float -- determines the multiplicative factor for the mean in the lognormal distribution 
    s -- float -- determines the multiplicative factor for the sigma in the lognormal distribution

    Returns
    ---------
    ne_fluct -- (N, N, N) numpy array -- lognormal fluctuations of the electron density
    '''
    xvec_length = xvector_length(N, 3, pixsize, subcube = False)
    if Lambda_max is not None:
        if indices:
            kmin = (N*pixsize/2) / Lambda_max
        else: # Mask all k modes that are smaller than kmax, corresponds to larger than Lambda_max
            kmin = np.pi/Lambda_max
    else: 
        kmin = 1
    if Lambda_min is not None:
        if indices:
            kmax = (N*pixsize/2) / Lambda_min
        else: # Mask all k modes that are larger than kmin, corresponds to smaller than Lambda_min
            kmax =  np.pi/Lambda_min
    else:
        kmax = N
    if mean:
        fc = pyFC.LogNormalFractalCube(ni=N, nj=N, nk=N, kmin = kmin, kmax = kmax, mean=mu * np.mean(ne_mean(xvec_length, r500)), sigma= s *np.mean(ne_mean(xvec_length, r500)), beta=-(xi -2))
    else: 
        fc = pyFC.LogNormalFractalCube(ni=N, nj=N, nk=N, kmin = kmin, kmax = kmax, mean=mu * np.mean(ne_beta(xvec_length)), sigma= s *np.mean(ne_beta(xvec_length)), beta=-(xi -2))
    fc.gen_cube()

    ne_fluct = fc.cube
    return ne_fluct

def magnetic_field_crossproduct(kvec, field):
    """
    Do the cross product of i*k and A(k), keeping in mind complex conjugate symmetries.

    Way faster than the old method that used loops, but only tested for even amount of N.
    And a bit more complex/obscure in the implementation. But gives exactly
    the same results as the loop method. 

    In the case that N=even (say 64)
    the complex conjugate symmetry is destroyed by the cross product
    because for index 32, the value of k_vec = pi but for -32 it's also pi. 
    But it should be -pi to keep complex conjugate symmetry. 
    # But -pi is pi in the Fourier plane because it flips there. 
    
    So it goes wrong in (64*63 - 64) cases, because that's how many points you can
    find in 3 dimensions where the coordinate on one axis is 32. 
    
    We can fix this by just doing the cross product in the half of the Fourier cube
    and taking the complex conjugate, just like how we determined the Field. 
    """
    
    fourier_B_field = np.zeros((N,N,N//2+1,3),dtype=ctype)
    # The fourier frequencies are different for (un)even N
    Neven = N%2 # add one to loops if N is uneven.
    # ONLY TESTED FOR EVEN N
                
    # all kz modes, all ky modes, half of the fourier cube, thus z=1 to N//2 (because z=0 and z//2 are special)
    fourier_B_field[:,:,1:N//2+Neven] = np.cross(1j*kvec[:,:,1:N//2+Neven], field[:,:,1:N//2+Neven],axis=-1)
                
    if Neven == 0:
        # We have an even amount of N, so do not forget the j = N//2
        # plane. It's conjugate symmetry is special because -N//2 = N//2
        # Similarly, the j=0 plane is also conjugate symmetric in the y-axis
        
        
        # We can generate half of this plane, since it's symmetric in the y axis
        # and then impose symmetry on the plane itself.
        # This is the equivalent to generating a 2D plane density field
                
        # The z=N//2 plane
        z = N//2
        # all kz modes, but start on kx at i=1 and end at N//2, because those two axes are special
        fourier_B_field[1:N//2,:,z] = np.cross(1j*kvec[1:N//2,:,z], field[1:N//2,:,z],axis=-1)      
        # The other half of the plane, complex conjugate symmetric (Hermitian symmetric)
        # Careful to also np.roll(1) in the np.flip, since otherwise we adjust the z=0 axis
        # e.g. for N=6 kx=[0, 1, 2, 3,-2,-1], so np.flip gives [-1,-2,3,2,1,0] and np.roll(flip) gives
        #                 [0,-1,-2, 3, 2, 1] as we want.
        # So for a 2D array, in this case we roll over the axis that is length N (axis=1 in this case)
        fourier_B_field[N//2+1:,:,z] = np.conj(np.roll(np.flip(fourier_B_field[1:N//2,:,z],axis=(0,1)),1,axis=1))    

        # The z=0 plane
        z = 0
        # all kz modes, but start on kx at i=1 and end at N//2, because those two axes are special
        fourier_B_field[1:N//2,:,z] = np.cross(1j*kvec[1:N//2,:,z], field[1:N//2,:,z],axis=-1)      
        # The other half of the plane, complex conjugate symmetric (Hermitian symmetric)
        fourier_B_field[N//2+1:,:,z] = np.conj(np.roll(np.flip(fourier_B_field[1:N//2,:,z],axis=(0,1)),1,axis=1))    
        

        # Don't forget the x=N//2 column, which we can generate half for
        # ky modes up to half (N//2)
        fourier_B_field[N//2,:N//2,N//2] = np.cross(1j*kvec[N//2,:N//2,N//2], field[N//2,:N//2,N//2],axis=-1)       
        # The other half is complex conjugate. Don't have to roll here, because only 1 axis
        fourier_B_field[N//2,N//2+1:,N//2] = np.conj(np.flip(fourier_B_field[N//2,1:N//2,N//2],axis=0)) 

        # And the x=0 column 
        fourier_B_field[0,:N//2,N//2] = np.cross(1j*kvec[0,N//2,:N//2], field[0,N//2,:N//2],axis=-1)       
        # The other half is complex conjugate
        fourier_B_field[0,N//2+1:,N//2] = np.conj(np.flip(fourier_B_field[0,1:N//2,N//2],axis=0)) 
        
        # same for when kz=0. Do the x=N//2 column
        fourier_B_field[N//2,:N//2,0] = np.cross(1j*kvec[N//2,:N//2,0], field[N//2,:N//2,0],axis=-1)       
        # The other half is complex conjugate
        fourier_B_field[N//2,N//2+1:,0] = np.conj(np.flip(fourier_B_field[N//2,1:N//2,0],axis=0)) 
        
        # And the kz=0, x=0 column, which we can also generate half for. 
        fourier_B_field[0,:N//2,0] = np.cross(1j*kvec[0,:N//2,0], field[0,:N//2,0],axis=-1)
        # The other half is complex conjugate
        fourier_B_field[0,N//2+1:,0] = np.conj(np.flip(fourier_B_field[0,1:N//2,0],axis=0)) 
                    
                
        # Now some numbers are their own complex conjugate.
        # i.e., they are real.
        fourier_B_field[0, 0, N//2] = fourier_B_field[0,0,N//2].real
        fourier_B_field[0, N//2, 0] = fourier_B_field[0,N//2,0].real
        fourier_B_field[N//2, 0, 0] = fourier_B_field[N//2, 0, 0].real
        
        fourier_B_field[0, N//2, N//2] = fourier_B_field[0,N//2,N//2].real
        fourier_B_field[N//2, N//2, 0] = fourier_B_field[N//2,N//2,0].real
        fourier_B_field[N//2, 0, N//2] = fourier_B_field[N//2, 0, N//2].real
        
        fourier_B_field[N//2, N//2, N//2] = fourier_B_field[N//2, N//2, N//2].real
        
    # Don't forget that the [0,0] component of the field has to be 0
    fourier_B_field[0,0,0] = 0 + 1j*0   
    
    # Now we don't have to generate the modes in the other half of the Fourier cube
    # because it's a redundant part, so we can just use irfftn    
    
    return fourier_B_field

def polynomial(x, A, B, C):
    '''
    a second order polynomial function:
    f(x,y) = A * x ** 2  + B * x ** 2 + C
     
    x       -- numpy array containing x coordinates
    A, B, C -- coefficients
    '''
    return A * x ** 2  + B * x + C

def poly(x, a, b, c, d, e, f):
    return a * x**5 + b*x**4 + c*x**3 + d * x**2 + e*x + f
def normalise_ne_field(ne_fluct, xvec_length, mean = True, r500 = 925):
    '''
    Function to normalize the ne field such that it follows the mean_ne profile
    '''
    average_profile = np.full((N, N, N), np.mean(ne_fluct))
    c = N//2 - 1
    if mean == True:
        ne_3d = ne_fluct/average_profile.reshape(N,N,N) * ne_mean(xvec_length, r500).reshape(N, N, N)
        ne_3d[c,c,c] = ne_mean(0, r500) # Electron density in center of cluster
    else: 
        ne_3d = ne_fluct/average_profile.reshape(N,N,N) * ne_beta(xvec_length).reshape(N, N, N)
        ne_3d[c, c,c ] = ne_beta(0)
    
    return ne_3d

def normalise_Bfield(ne_3d, ne0, B_field, eta, B0, xvec_length, subcube=False):
    """
    Normalise the B field such that it follows the electron density profile

    ne_3d   -- electron density at every point in the 3D space. shape (N,N,N)
    ne0     -- electron density in the center of the cluster
    B_field -- Magnetic field at every point in the 3D space. shape (N,N,N,3)
    eta     -- Proportionality of B to n_e
    B0      -- Mean magnetic field in center
    xvec_length -- vector defining the radius wrt the center of the image in (N,N,N)
    subcube -- Because its symmetric, only need a small part of cube (only positive quadrant)

    """
    B_field_amplitude = np.linalg.norm(B_field[:,:,N//2-1,:],axis=2)
    average_profile = np.full((N, N, N), np.mean(B_field_amplitude))

    if subcube:
        # make the full cubes for the normalisation of the B field
        # Expand 1/8th of the cube to the full cube. 
        ne_3d = cube_from_subcube(ne_3d, N)
        average_profile = cube_from_subcube(average_profile, N)

    # Normalise the B field to mean 1*B0 and then multiply by the normalised profile
    B_field_norm = B_field/average_profile.reshape(N,N,N,1)* B0 * (np.power(ne_3d / ne0, eta)).reshape(N,N,N,1)
    # Special case, central point
    c = N//2-1
    # Make sure B field is B0 muGauss in center
    B_field_norm[c,c,c] = B0/np.sqrt(3) # so it's a 3D vector with length 1*B0, in a particular direction

    return B_field_norm, ne_3d

def verify_normalisation():
    """
    Plot a check to see if B field is normalised as we expect
    """
    B_field_amplitude = np.linalg.norm(B_field,axis=3)

    # theoretical profile up to 1500 kpc
    r = np.logspace(0,3.2,500) 
    ne = beta_model(r, ne0, rc, beta)

    # Profile of B field
    all_r, profile = radial_profile(B_field_amplitude[:,:,N//2-1], center=[N//2-1,N//2-1])
    all_r *= pixsize

    fig, ax = plt.subplots(figsize=(8,8))
    plt.plot(all_r,profile,label='Magnetic field simulated',marker='o',markersize=2)
    plt.plot(r, B0*(ne/(ne[0]))**eta , label='Magnetic field model',c='k')

    all_r, profile_ne_box = radial_profile(ne_3d[:,:,N//2-1],center=[N//2-1,N//2-1])
    all_r *= pixsize
    plt.plot(all_r, B0*(profile_ne_box/profile_ne_box[0])**eta
             ,label='Magnetic field model from box',c='C1')

    plt.xlabel('Radius [kpc]')
    plt.ylabel('Magnetic field strength')
    # plt.yscale('log')
    plt.legend()
    plt.xlim(1,N*pixsize)
    plt.show()

def RM(n_e,B_field,pixsize,axis):
    """
    Calculate Rotation measure by integrating over a certain axis
    (Riemann sum)
    
    Integrating over one pixel is integration over pixsize*1000 parsecs.
    """
    return 0.81*pixsize*1e3*np.sum(n_e*B_field[:,:,:,axis],axis=axis)

def RM_halfway(n_e,B_field,pixsize,axis):
    """
    Calculate Rotation measure by 'integrating' over a certain axis.
    Now only integrate over half the axis
    
    
    Integrating over one pixel is integration over pixsize*1000 parsecs.
    """
    N = len(n_e)
    # For keeping track over which axis we want to do the integrating
    if axis == 0:
        N0 = N//2
        N1 = N
        N2 = N
    elif axis == 1:
        N0 = N 
        N1 = N//2
        N2 = N
    elif axis == 2:
        N0 = N 
        N1 = N
        N2 = N//2
    else:
        raise ValueError("Axis not implemented")
        
    return 0.81*pixsize*1e3*np.sum(n_e[:N0,:N1,:N2]*B_field[:N0,:N1,:N2,axis],axis=axis)

def plotRMimage(RMimage, pixsize):

    extent = [-(N//2+1)*pixsize, (N//2)*pixsize, -(N//2+1)*pixsize, (N//2)*pixsize]

    plt.imshow(RMimage,extent=extent,origin='lower')
    cbar = plt.colorbar()
    cbar.set_label("RM [rad m$^{-2}$]")
    plt.xlabel('x [kpc]')
    plt.ylabel('y [kpc]')
    plt.show()

def calc_phi_obs(phi_intrinsic, RM, wavelength):
    """
    Calculate observed polarisation angle at a certain wavelength
    Given the intrinsic polarisation angle and the rotation measure
    """
    phi_obs = (phi_intrinsic + RM * wavelength**2) % (2*np.pi)
    return phi_obs 

def StokesQU_image(phi_obs, polint_intrinsic):
    """
    Calculate the Stokes Q and U flux given the intrinsic polarised intensity 
    and the polarisation angle
    """
    Q = polint_intrinsic / np.sqrt(1+np.tan(2*phi_obs)**2)
    
    U = np.sqrt(polint_intrinsic**2 - Q**2)
    
    # Positive Q for angle between -pi/2 and pi/2
    # which in our definition is angle > 3/2 pi or < 1/2 pi
    # Thus negative Q for angles between 1/2pi and 3/2 pi
    negQ = np.bitwise_and(np.pi/2 <= phi_obs, phi_obs <= 3*np.pi/2)
    Q[negQ] *= -1
    
    # Negative U for angles larger than pi
    negU = phi_obs > np.pi 
    U[negU] *= -1
    
    return Q, U

def addnoise(images, rms):
    noiseimages = []
    for image in images:
        noiseimage = image + np.random.normal(loc=0.0,scale=rms,size=image.shape) 
        noiseimages.append(noiseimage)
        
    return noiseimages

def convolve_with_beam(images, FWHM, pixsize=1.0):
    """
    Convolve the images with a (circular) Gaussian beam with FWHM given in kpc,
    which is equal to the amount of pixels if 1 pixel is 1 kpc
    """
    print ("Convolving with a beam FWHM of %i kpc"%FWHM)

    # FWHM to standard deviation divided by pixel size 
    std = FWHM/(2*np.sqrt(2*np.log(2))) / pixsize

    print ("Which is a standard deviation of %.1f pixels"%std)

    if std < 2:
        print ("Since the beam resolution (FWHM %.1f kpc or std %.1f kpc) is so close to the simulated resolution (%.1f kpc), NOT smoothing"%(FWHM,std*pixsize, pixsize))
        return images 

    beam = Gaussian2DKernel(std)
    convolved = []
    for image in images:
        convolved.append(convolve(image,beam,boundary='extend',normalize_kernel=True))
    return convolved

def plot_radial_profile(Polint, coldens, ylog=True):
    """
    Plot the radial profile of the Polint image
    """
    # Check the radial profile of the polint image
    all_r, profile = radial_profile(Polint, center=[N//2-1,N//2-1])

    # Get coldens also as function of radius
    all_r, coldens_profile = radial_profile(coldens, center=[N//2-1,N//2-1])
    # Now coldens_profile corresponds to polint profile, because all_r is the same
    # , because image size is the same.

    fig, axes = plt.subplots(1, 2, figsize=(8,5))

    plt.sca(axes[0])
    all_r *= int(pixsize) # Make sure r is in kpc and not in pixels
    
    plt.plot(all_r[10:-10],profile[10:-10],label='Polint profile',marker='o',markersize=3,alpha=0.6)
    plt.legend()
    plt.xlabel('r [kpc]')
    plt.ylabel("Polarisation fraction")
    if ylog: 
        plt.yscale('log')

    plt.sca(axes[1])

    plt.plot(coldens_profile[10:-10],profile[10:-10],label='Polint profile',marker='o',markersize=3)
    if ylog: 
        plt.yscale('log')
    plt.xscale('log')
    plt.legend()
    plt.xlabel('Electron column density [cm$^{-2}$]')
    plt.ylabel("Polarisation fraction")
    # plt.xlim(1,64)
    plt.show()

def columndensity(n_e, pixsize, axis):
    """
    We can calculate the column density image by integrating over a certain axis (Riemann sum)
    pixels are given in kpc, so we should convert that to cm
    """
    kpc = 3.08567758e21 #centimeters
    return pixsize*kpc*np.sum(n_e,axis=axis)

def beta_model(r, ne0=0.0031, r_c=341, beta=0.77):
    """
    Use beta model for cluster electron density profile

    default is Remi Adam's parameters for Abell 2256
    
    r    -- float or array -- radius in kpc [kpc] 
    ne0  -- float  -- central electron density in cm^-3
    r_c  -- float  -- core radius in kpc
    beta -- float  -- beta parameter
    
    """    
    return ne0 * (1+(r/r_c)**2)**(-3*beta/2)

def BandAfieldfiles(N,pixsize,xistr,Lambda_max,itstr):
    if Lambda_max is None:
        vectorpotential_file = savedir+'Afield_N=%i_%s%s.npy'%(N,xistr,itstr)
        Bfield_file = savedir+'Bfield_N=%i_p=%i_%s%s.npy'%(N,pixsize,xistr,itstr)
    else:
        # Now its not scale invariant, so pixel size is important also for Fourier transform.
        vectorpotential_file = savedir+'Afield_N=%i_p=%i_%s_Lmax=%i%s.npy'%(N,pixsize,xistr,Lambda_max,itstr)
        Bfield_file = savedir+'Bfield_N=%i_p=%i_%s_Lmax=%i%s.npy'%(N,pixsize,xistr,Lambda_max,itstr)
    return vectorpotential_file, Bfield_file

def create_paramstring(N,pixsize,B0,xistr,eta,sourcename,Lambda_max,itstr,beamstr,redshift_dilution):
    if Lambda_max is None:
        paramstring = 'N=%i_p=%i_B0=%.1f_%s_eta=%.1f_s=%s%s%s'%(N,pixsize,B0,xistr,eta,sourcename,itstr,beamstr)
    else:
        paramstring = 'N=%i_p=%i_B0=%.1f_%s_eta=%.1f_s=%s_Lmax=%i%s%s'%(N,pixsize,B0,xistr,eta,sourcename,Lambda_max,itstr,beamstr)
    if redshift_dilution:
        paramstring += '_zd'
    return paramstring

def check_results_already_computed():
    """
    Check whether we already have a 2D RM image with the current parameters
    or perhaps with a different value of B_0

    RETURNS
    a string that is either
        'fully computed'
        'partially computed'
        'not computed'
    """
    savedir2 = savedir + 'after_normalise/%s/'%sourcename

    # First check if the result with the given B0 is already computed
    paramstring = create_paramstring(N,pixsize,B0,xistr,eta,sourcename,Lambda_max,itstr,beamstr,redshift_dilution)
    if os.path.isfile(savedir2+'RMimage_%s.npy'%paramstring) and os.path.isfile(savedir2+'RMhalfconvolved_%s.npy'%paramstring):
        return 'fully computed'
    else:
        # Check if the result with B0=1 is already computed. We can use it
        # to compute the result with any other B0
        paramstring = create_paramstring(N,pixsize,1,xistr,eta,sourcename,Lambda_max,itstr,beamstr,redshift_dilution)
        if os.path.isfile(savedir2+'RMimage_%s.npy'%paramstring) and os.path.isfile(savedir2+'RMhalfconvolved_%s.npy'%paramstring):
            return 'partially computed'
        else:
            return 'not computed'

def computeRMimage_from_file():
    """
    If we already have an RM image with B0=1, we can simply scale it to any other B0
    because we're simply doing  X * integral(B*ne) dr = X * RM
    """
    savedir2 = savedir + 'after_normalise/%s/'%sourcename
    # Load the B0=1 results
    paramstring = create_paramstring(N,pixsize,1,xistr,eta,sourcename,Lambda_max,itstr,beamstr,redshift_dilution)
    
    RMimage          = np.load(savedir2+'RMimage_%s.npy'%paramstring)
    RMimage_half     = np.load(savedir2+'RMimage_half_%s.npy'%paramstring)
    RMconvolved      = np.load(savedir2+'RMconvolved_%s.npy'%paramstring)
    RMhalfconvolved  = np.load(savedir2+'RMhalfconvolved_%s.npy'%paramstring)

    # Scale with whatever B0 we have now
    RMimage *= B0
    RMimage_half *= B0
    RMconvolved *= B0
    RMhalfconvolved *= B0

    return RMimage, RMimage_half, RMconvolved, RMhalfconvolved

def params_for_testing():
    """
    Function that does nothing. Only used for debugging.
    """
    xi = 4
    N = 1024
    eta = 0.5
    B0 = 5
    sourcename = 'G115.16-72.09'
    Lambda_max = None
    pixsize = 3
    dtype = 32
    garbagecollect = True
    alice = False
    doUPP = False
    noXray = False
    redshift_dilution = True
    iteration = 0
    beamsize = 13.91483647
    recompute = False

    cmd = 'python3 /net/lofar4/data1/osinga/phd/year1/PlanckESZ_RM/analysis/magneticfieldmodel_paper2.py -xi %.3f -N 1024 -eta %.4f -B0 1 -s %s -pixsize 3.0 -dtype 32 -garbagecollect True -iteration %i -beamsize %.2f'%(xi, eta, sourcename, iteration, beamsize)
    print(cmd)
    
    return

if __name__ == '__main__':

    ########### Example parameters
    # # Power law -4 makes magnetic field -2 
    # xi = 4
    # N = 1024 # amount of pixels
    # pixsize = 1 # 1 pixel = this many kpc
    # eta = 0.5
    # B0 = 5.0

    ###########
    parser = argparse.ArgumentParser(description='Create a magnetic field model with user specified parameters')
    parser.add_argument('-s','--sourcename', help='Cluster name, for saving purposes', type=str)
    parser.add_argument('-reffreq','--reffreq', help='Reference Frequency in MHz (i.e. center of the band)', type=float)
    parser.add_argument('-cz','--cz', help='Cluster redshift', type=float)
    parser.add_argument('-xi','--xi', help='Vector potential spectral index (= 2 + {Bfield power law spectral index}, default Kolmogorov )', type=float, default=5.67)
    parser.add_argument('-mean', '--mean', help = 'Boolean that determines whether or not to use the mean n_e profile, default to True', type = bool, default = True)
    parser.add_argument('-r500', '--r500', help = 'Cluster R500 in kpc, default to 925 kpc', type = float, default = 925)
    parser.add_argument('-N' ,'--N', help='Amount of pixels (default 512, power of 2 recommended)', type=int, default=512)
    parser.add_argument('-pixsize','--pixsize', help='Pixsize in kpc. Default 1 pix = 3 kpc.', default=3.0, type=float)
    parser.add_argument('-eta','--eta', help='Exponent relating B field to electron density profile', type=float)
    parser.add_argument('-B0','--B0', help='Central magnetic field strength in muG', type=float)
    parser.add_argument('-lmax','--lambdamax', help='Maximum scale in kpc. Default None (i.e. max size of grid/2).', default=None, type=float)
    parser.add_argument('-dtype','--dtype', help='Float type to use 32 bit (default) or 64 bit', default=32, type=int)
    parser.add_argument('-garbagecollect','--garbagecollect', help='Let script manually free up memory (Default True)', default=True, type=bool)
    ## todo
    # parser.add_argument('-doUPP','--doUPP', help='If X-ray is not found, continue with UPP', default=False, type=bool)
    parser.add_argument('-iteration' ,'--iteration', help='For saving different random initializations', type=int, default=0)
    parser.add_argument('-beamsize' ,'--beamsize', help='Image beam size in arcsec, for smoothing.', type=float, default=20.0)
    parser.add_argument('-recompute' ,'--recompute', help='Whether to recompute even if data already exists', type=bool, default=False)
    parser.add_argument('-ne0' ,'--ne0', help='Central electron density in beta model.', type=float, default=0.0031)
    parser.add_argument('-rc' ,'--rc', help='Core radius in kpc', type=float, default=341)
    parser.add_argument('-beta' ,'--beta', help='Beta power for beta model', type=float, default=0.77)
    parser.add_argument('-mu', '--mu', help = 'Mean of the fluctuations in the electron density', type = float, default = 1.0)
    parser.add_argument('-sigma', '--sigma', help = 'Standard deviation of the fluctuations in the electron density', type = float, default = 0.2)
    parser.add_argument('-savedir' ,'--savedir', help='Where to save results', type=str, default="./")

    args = vars(parser.parse_args())

    mean = args['mean']
    r500 = args['r500']
    xi = args['xi']
    N = args['N']
    eta = args['eta']
    B0 = args['B0']
    sourcename = args['sourcename']
    cz = args['cz']
    reffreq = args['reffreq']
    Lambda_max = args['lambdamax']
    pixsize = args['pixsize']
    mu = args['mu']
    sigma = args['sigma']
    dtype = args['dtype']
    garbagecollect = args['garbagecollect']
    # doUPP = args['doUPP'] ## TODO
    # noXray = args['noXray'] ## TODO
    redshift_dilution = True # Calculate the RM in the cluster frame (True) or in observed frame (False)
    iteration = args['iteration']
    beamsize = args['beamsize']
    recompute = args['recompute']
    ne0 = args['ne0']
    rc = args['rc']
    beta = args['beta']
    savedir = args['savedir']
    if savedir[-1] != "/":
        savedir += "/"
    if not os.path.exists(savedir):
        print("Creating output directory %s"%savedir)
        os.mkdir(savedir)

    # Whether to save the normalised B field, RM images, etc (everything after normalising the B field)
    saveresults = True 

    if Lambda_max is not None:
        if Lambda_max >= N*pixsize/2:
            print("INFO: Lambda_max is set equal to maximum allowed scale by the grid.")
            Lambda_max = None

    itstr = '_it'+str(iteration)
    print("Saving results as iteration %s"%itstr)

    if dtype == 32:
        ftype = np.float32
        ctype = np.complex64
    elif dtype == 64:
        ftype = np.float64
        ctype = np.complex128
    else:
        sys.exit("CANNOT SET DTYPE TO FLOAT%i. EXITING!!!!!"%dtype)

    if int(sys.version[0]) < 3:
        sys.exit("PLEASE USE PYTHON3 TO RUN THIS CODE. EXITING")


    #### TODO , use universal pressure profile?
    # usingUPP = False # todo
    # if params is None:
    #     print ("Cannot find X-ray observations for cluster %s"%sourcename)
    #     if not doUPP:
    #         sys.exit("Exiting..")
    #     else:
    #         print ("Using UPP function to approximate n_e")
    #         usingUPP = True

    # else: # X-ray is found, do script with X-ray or stop. 
    #     if noXray:
    #         print("User has decided not to proceed with X-ray observations")
    #         sys.exit("Exiting..")

    
    # Calculate the resolution we have at cluster redshift with an X arcsec beam
    resolution = (cosmo.kpc_proper_per_arcmin(cz)*beamsize*u.arcsec).to(u.kpc)
    FWHM = resolution.value # in kpc
    std_gausskernel = FWHM/(2*np.sqrt(2*np.log(2))) # in kpc

    if FWHM < pixsize*5: # 5 is approximately 2 * 2sqrt(2ln(2))  (because we want at least 2 pix)
        # Set it automatically so the FWHM corresponds to 5*pixsize at cluster redshift
        print(f"WARNING: User input angular resolution of {beamsize} arcsec corresponds to physical resolution of {FWHM:.2f} kpc (FWHM).")
        FWHM = pixsize*5 # kpc
        beamsize = (FWHM*u.kpc/(cosmo.kpc_proper_per_arcmin(cz).to(u.kpc/u.arcsec))).to(u.arcsec).value
        print(f"WARNING: However, models are being ran with p={pixsize} kpc. The code will smooth to {FWHM} kpc automatically. This corresponds to a beam size of {beamsize:.2f} arcsec instead. Please keep this in mind.")
    
    beamstr = '_b%.2fasec'%beamsize 

    xistr = 'xi=%.2f'%xi

    print ("Using parameters:")
    print (" xi=%.2f (n=%.2f)"%(xi,xi-2))
    print (" N=%i"%N)
    print (" eta=%.1f"%eta)
    print (" B0=%.1f"%B0)
    print (" pixsize=%.1f"%pixsize)
    print (" sourcename= %s"%sourcename)
    print (" cz= %.2f"%(cz))
    print (" Lambda_max= %s"%Lambda_max)
    print (" Beam FWHM = %.1f arcsec"%beamsize)
    print (" Beam FWHM = %.1f kpc"%FWHM)
    print (" dtype= float%i"%dtype)
    print (" Manual garbagecollect= %s"%str(garbagecollect))
    if mean:
        print("r500 = %.2f"%(r500))
    else:
        print (" ne0= %.2f"%(ne0))
        print (" rc= %.2f"%(rc))
        print (" beta= %.2f"%(beta))

    # The electron density model (can replace by own model)
    def ne_beta(r, ne0=ne0, rc=rc, beta=beta):
        return beta_model(r, ne0, rc, beta)


    # Randomly set an intrinsic polarisation angle (uniform)
    phi_intrinsic = 45*np.pi/180 # degrees to radians
    #Observed wavelength of radiation in meters
    wavelength = (speed_of_light/(reffreq*u.MHz)).to(u.m).value  

    if not recompute:
        status = check_results_already_computed()
        if status == 'fully computed':
            dtime = time.time()-starttime
            print ("Script fully finished. Took %.1f seconds to check results"%(dtime))
            sys.exit("Results already computed and recompute=False, exiting.")
        # Otherwise status = partially computed or not computed, continue. 

    if status == 'not computed' or recompute: 
        # We dont have the resulting RM image or user forces recompute, so compute it.
        
        # Starting from Afield, Bfield or from scratch (if recompute=True)
    
        # Set indices=True if we are computing it the fast way, with the gaussian_random_field3D function
        Peff = lambda k: model_xi(k, xi, N, Lambda_max, indices=True)

        # The files where the vector potential and B field are / will be saved
        vectorpotential_file, Bfield_file = BandAfieldfiles(N,pixsize,xistr,Lambda_max,itstr)

        # Boolean to track whether maybe we have computed unnormalised A or B field before
        already_computed = False
        if os.path.isfile(vectorpotential_file) and not recompute:
            already_computed = True
            print ("Found a saved version of the vector potential with user defined parameters:")
            print (" N=%i \n xi=%.2f Lmax=%s"%(N,xi,Lambda_max))

            print ("Checking if magnetic field was also already computed..")
            if os.path.isfile(Bfield_file):
                print ("It is, so not loading vector potential from file")
            else:
                print ("It is not, loading vector potential from file..")
                field = np.load(vectorpotential_file)

        else:
            print ("Generating random field for vector potential A.")
            # Every component of the 3D cube of A is a vector, since A is a vector field.
            # So A has shape (N,N,N,3)
            # So we just generate three randomfield cubes, and each cube will be one dimension of the vector.

            # Make field without the redundant Fourier components
            field = np.zeros((N,N,N//2+1,3),dtype=ftype) + 1j*np.zeros((N,N,N//2+1,3),dtype=ftype)

            # Get the normalised index length in 3D space
            k_length = kvector_lengthonly(N)

            print ("Random field x-dimension..")
            field[:,:,:,0] = gaussian_random_field3D(N, Peff, k_length)
            print ("Random field y-dimension..")
            field[:,:,:,1] = gaussian_random_field3D(N, Peff, k_length)
            print ("Random field z-dimension..")
            field[:,:,:,2] = gaussian_random_field3D(N, Peff, k_length)

        # Save the Fourier transform of the vector potential to a file if it didnt exist yet
        if not already_computed: 
            print ("Saving fourier vector potential to %s, such that it can be used again"%vectorpotential_file)
            # Fourier vector potential only depends on number of pixels and assumed power law index
            #np.save(vectorpotential_file, field)

        already_computed = False # Also check B field. Might not have been already computed
        if os.path.isfile(Bfield_file) and not recompute:
            already_computed = True
            print ("Found a saved version of the magnetic field with user defined parameters:")
            print (" N=%i \n xi=%.2f \n pixsize=%i Lmax=%s"%(N,xi,pixsize,Lambda_max))
            print ("Loading from file..")
            B_field = np.load(Bfield_file)

        else:
            print ("Generating k vector in (%i,%i,%i,3) space"%(N,N,N//2))
            kvec = kvector(N, 3, pixsize)

            print ("Calculating magnetic field using the crossproduct Equation")
            # Fourier B field = Cross product  B = ik \cross A 
            field = magnetic_field_crossproduct(kvec, field)
            # memoryUse = python_process.memory_info()[0]/2.**30
            # print('Memory used: %.1f GB'%memoryUse)
            del kvec # Huge array which we dont need anymore 
            if garbagecollect: 
                timeg = time.time()
                print ("Deleted kvec. Collecting garbage..")
                gc.collect()
                memoryUse = python_process.memory_info()[0]/2.**30
                print('Memory used: %.1f GB'%memoryUse)
                print ("Garbage collected in %i seconds"%(time.time()-timeg))

            # B_field = scipy.fftpack.ifftn(fourier_B_field,axes=(0,1,2))*N**3
            if int(sys.version[0]) >= 3:
                # B field is the inverse fourier transform of fourier_B_field
                run_ift = pyfftw.builders.irfftn(field,s=(N,N,N),axes=(0,1,2)
                    , auto_contiguous=False, auto_align_input=False, avoid_copy=True,threads=48)
                field = run_ift()
                B_field = field # re-name it B-field for clarity
                # memoryUse = python_process.memory_info()[0]/2.**30
                # print('Memory used: %.1f GB'%memoryUse)
                if garbagecollect: 
                    timeg = time.time()
                    print ("Ran IFFT. Collecting garbage..")
                    gc.collect()
                    memoryUse = python_process.memory_info()[0]/2.**30
                    print('Memory used: %.1f GB'%memoryUse)
                    print ("Garbage collected in %i seconds"%(time.time()-timeg))

            else:
                sys.exit("WARNING, USING PYTHON2. PLEASE RUN WITH PYTHON3. EXITING")
                # B_field = pyfftw.interfaces.scipy_fftpack.ifftn(fourier_B_field,axes=(0,1,2))
            print ("Resulting magnetic field shape: %s"%str(B_field.shape))

        if not already_computed: 
            print ("Saving unnormalised magnetic field to %s, such that it can be used again"%Bfield_file)
            # Magnetic field also depends on the pixel size assumed (since it affects K)
            #np.save(Bfield_file, B_field)

        ## Using radial symmetry in a way where we can only use 1/8th of the cube
        ## we can calculate ne_3d about 6x faster for N=1024
        subcube = False
        multiprocessing = False

        # Vector denoting the real space positions. The 0 point is in the middle.
        # Now runs from -31 to +32 which is 64 values. Or 0 to +32 when subcube=True
        # The norm of the position vector
        xvec_length = xvector_length(N, 3, pixsize, subcube=False)

        # Let's test it with a random electron density profile 
        # sourcename = 'G021.09+33.25' # A2204. Cool-core cluster.  Figure 3 in Santos paper
        # sourcename = 'G282.49+65.17' # field with 25 polarised components
        # sourcename = 'G125.58-64.14' # A119
        ne_fluct = gen_ne_fluct(xi = xi, Lambda_max=Lambda_max, indices=True, Lambda_min=None, mu = mu, s = sigma, mean = mean, r500 = r500)
        
        if multiprocessing:
            print ("Normalising profile (multiprocessing) with electron density profile")
            pool = mp.Pool(processes=8)
            f = partial(normalise_ne_field, ne_fluct, mean, r500)
            ne_3d = np.asarray(pool.map(f, xvec_length))
            pool.close()
            pool.join()
        else:
            print ("Normalising profile with electron density profile")
            ne_3d = normalise_ne_field(ne_fluct, xvec_length, mean, r500)

        if subcube:
            c = 0 # then the center pixel is the first one, because the subcube is only the positive subset
        else:
            # Make sure n_e is not infinite in the center. Just set it to the pixel next to it
            c = N//2-1

        # Make sure n_e is not infinite in the center. Just set it to the pixel next to it
        #ne_3d[c,c,c] = ne_3d[c,c+1,c] # This is not necessary for us at the moment 
        ne0 = ne_3d[c,c,c] # Electron density in center of cluster

        # Normalise the B field such that it follows the electron density profile ^eta
        B_field_norm, ne_3d = normalise_Bfield(ne_3d, ne0, B_field, eta, B0, xvec_length, subcube)
        # memoryUse = python_process.memory_info()[0]/2.**30
        # print('Memory used: %.1f GB'%memoryUse)
        del B_field # We dont need B field unnormalised anymore 
        del xvec_length # We dont need xvec_length anymore
        if garbagecollect: 
            timeg = time.time()
            print ("Deleted B_field and xvec_length. Collecting garbage..")
            gc.collect()
            memoryUse = python_process.memory_info()[0]/2.**30
            print('Memory used: %.1f GB'%memoryUse)
            print ("Garbage collected in %i seconds"%(time.time()-timeg))


        # Calculate the B_field amplitude (length of the vector)
        # B_field_amplitude_nonorm = np.copy(B_field_amplitude)
        # B_field_amplitude = np.linalg.norm(B_field_norm,axis=3)

        print ("Calculating rotation measure images.")
        # Calculate the RM by integrating over the 3rd axis
        RMimage = RM(ne_3d,B_field_norm,pixsize,axis=2)
        # Also integrate over half of the third axis. For in-cluster sources
        RMimage_half = RM_halfway(ne_3d,B_field_norm,pixsize,axis=2)

        # Convolve the RM image with this resolution.
        # From here we can start to use float64 again, because the images are 2D
        RMconvolved, RMhalfconvolved = convolve_with_beam([RMimage,RMimage_half], FWHM, pixsize)

    elif status == 'partially computed':
        print(f"Loading RM image from file with B0=1, and scaling it to B0={B0}")
        RMimage, RMimage_half, RMconvolved, RMhalfconvolved = computeRMimage_from_file()
    
    else:
        raise ValueError(f"Status is {status} and it is not implemented what to do with this.")

    # plotRMimage(RMimage, pixsize)
    # plotRMimage(RMconvolved, pixsize)

    # Calculate observed polarisation angle, assuming a constant pol angle
    # and observing wavelength translated to the cluster redshift to account for redshift dilution
    if redshift_dilution:
        wavelength_cluster = wavelength / (1+cz)
        phi_obs = calc_phi_obs(phi_intrinsic, RMimage, wavelength_cluster) # Shape (N,N)
        # Calculate also observed polarisation angle if the screen is halfway inside the cluster
        phi_obs_inside = calc_phi_obs(phi_intrinsic, RMimage_half, wavelength_cluster)
    else:
        # Not taking it into account is wrong, but for legacy value, to reproduce old results
        phi_obs = calc_phi_obs(phi_intrinsic, RMimage, wavelength) # Shape (N,N)
        # Calculate also observed polarisation angle if the screen is halfway inside the cluster
        phi_obs_inside = calc_phi_obs(phi_intrinsic, RMimage_half, wavelength)


    # Convert the pol angle and polarised intensity (constant) to Stokes Q and U
    # polint intrinsic should be set such that we get fractional polarisation of 30%
    # at the edges of the cluster
    polint_intrinsic = 1 # Let's say 1 Jy/beam to start with?
    print ("Calculating Stokes Q and U images")
    Qflux, Uflux = StokesQU_image(phi_obs, polint_intrinsic)
    # Also for a screen inside the cluster (less rotation)
    Qflux_inside, Uflux_inside = StokesQU_image(phi_obs_inside, polint_intrinsic)

    # print ("Not adding Gaussian noise to the images.")
    Qconvolved, Uconvolved = convolve_with_beam([Qflux,Uflux], FWHM, pixsize)
    Qflux_inside, Uflux_inside = convolve_with_beam([Qflux_inside,Uflux_inside], FWHM, pixsize)

    polangle = np.arctan2(Uconvolved,Qconvolved)*0.5
    Polint = np.sqrt(Qconvolved**2+Uconvolved**2)

    polangle_inside = np.arctan2(Uflux_inside,Qflux_inside)*0.5
    Polint_inside = np.sqrt(Qflux_inside**2+Uflux_inside**2)

    if status != 'partially computed':
        # Calculate the column density image if its never been done before.
        coldens = columndensity(ne_3d, pixsize, axis=2)

        # and Integrate B field along the LOS. Not used yet in analysis.
        # We could simply scale this as well for models with new B0 // todo
        Bfield_integrated = np.sum(B_field_norm[:,:,:,2],axis=2)

    dtime = time.time()-starttime
    print ("Script calculations finished. Took %i seconds which is %.1f hours or %.1f days"%(dtime,dtime/3600.,dtime/86400.))

    if saveresults:
        savedir2 = savedir + 'after_normalise/%s/'%sourcename

        if not os.path.exists(savedir+'after_normalise/'):
            os.mkdir(savedir+'after_normalise/')

        if not os.path.exists(savedir2):
            os.mkdir(savedir2)
        print ("Saving results to %s"%savedir2)

        # B field is 25 GB for N=1024, so that's too much to save for all clusters.
        # np.save(savedir2+'Bfield_norm_N=%i_p=%i_B0=%.1f_xi=%i_s=%s.npy'%(N,pixsize,B0,xi,sourcename), B_field_norm)

        ### Update 18 oct: I had forgotten to put eta in the paramstring...
        paramstring = create_paramstring(N,pixsize,B0,xistr,eta,sourcename,Lambda_max,itstr,beamstr,redshift_dilution)

        # These images are only ~ 9 MB for N=1024 so thats fine.
        np.save(savedir2+'RMimage_%s.npy'%paramstring, RMimage)
        np.save(savedir2+'RMimage_half_%s.npy'%paramstring, RMimage_half)
        np.save(savedir2+'RMconvolved_%s.npy'%paramstring, RMconvolved)
        np.save(savedir2+'RMhalfconvolved_%s.npy'%paramstring, RMhalfconvolved)
        np.save(savedir2 + 'B_field_norm%s.npy'%paramstring, B_field_norm)

        np.save(savedir2+'Qconvolved_%s.npy'%paramstring, Qconvolved)
        np.save(savedir2+'Uconvolved_%s.npy'%paramstring, Uconvolved)
        np.save(savedir2+'Polintconvolved_%s.npy'%paramstring, Polint)
        np.save(savedir2+'Polint_halfconvolved_%s.npy'%paramstring, Polint_inside)

        if status != 'partially computed': # Then we can save them for the first time
            np.save(savedir2+'coldens_N=%i_p=%i_s=%s.npy'%(N,pixsize,sourcename), coldens)
            # Save integrated B field
            np.save(savedir2+'Bfield_integated_%s.npy'%paramstring,Bfield_integrated)

    dtime = time.time()-starttime
    print ("Script fully finished. Took %i seconds which is %.1f hours or %.1f days"%(dtime,dtime/3600.,dtime/86400.))
