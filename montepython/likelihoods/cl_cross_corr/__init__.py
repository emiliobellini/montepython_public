import os
import yaml
import itertools
import numpy as np
from montepython.likelihood_class import Likelihood
import montepython.io_mp as io_mp
import warnings
import pyccl as ccl
import shutil  # To copy the yml file to the outdir



class cl_cross_corr(Likelihood):

    # initialization routine

    def __init__(self, path, data, command_line):

        Likelihood.__init__(self, path, data, command_line)

        self.outdir = command_line.folder

        def find_file_cls(path, tracers):
            for p in itertools.permutations(tracers):
                fname = '{}/cls_{}_{}.npz'.format(path,p[0],p[1])
                if os.path.isfile(fname):
                    return fname
            raise IOError('File does not exists')


        def find_file_cov(path, tracers1, tracers2):
            for p_ref in itertools.permutations([tracers1,tracers2]):
                for p1 in itertools.permutations(p_ref[0]):
                    for p2 in itertools.permutations(p_ref[1]):
                        fname = '{}/{}/cov_{}_{}_{}_{}.npz'.format(path,data.cosmo_arguments['fiducial_cov'],p1[0],p1[1],p2[0],p2[1])
                        if os.path.isfile(fname):
                            return fname
            raise IOError('File does not exists')

        def get_bandpower_lims(eff_ell):
            bpw = np.zeros((eff_ell.size, 2))

            bpw[0] = np.array([0, 2 * eff_ell[0]])
            for i in range(1, eff_ell.size):
                l0 = bpw[i-1, 1] + 1
                l1 = 2 * eff_ell[i] - l0
                bpw[i] = np.array([l0, l1])

            return bpw


        # Read arguments
        with open(os.path.abspath(data.cosmo_arguments['params_dir'])) as f:
            self.params = yaml.safe_load(f)
        self.n_data_vectors = len(self.params['data_vectors'])
        shutil.copy2(os.path.abspath(data.cosmo_arguments['params_dir']),
                     self.outdir)

        # Load Cl's
        self.data = np.array([])
        used_tracers = np.array([])
        self.tracers_tosave = []
        self.ells_tosave = np.array([])
        for ndv in range(self.n_data_vectors):
            dv = self.params['data_vectors'][ndv]
            # Get ells and cls
            fname = find_file_cls(self.cov_cls,dv['tracers'])
            ells = np.load(fname)['ells']
            cls = np.load(fname)['cls']
            bpw = get_bandpower_lims(ells)
            dv['mask'] =  (bpw[:, 0]>=dv['ell_cuts'][0]) & (bpw[:, 1]<=dv['ell_cuts'][1])
            dv['ells'] = ells[dv['mask']]
            self.data = np.append(self.data,cls[dv['mask']])
            used_tracers = np.append(used_tracers,dv['tracers'])

            self.ells_tosave = np.append(self.ells_tosave, dv['ells'])
            self.tracers_tosave.append(dv['tracers'])

        used_tracers = np.unique(used_tracers)


        # Remove unused tracers
        for ntr, tr in enumerate(self.params['maps']):
            if tr['name'] not in used_tracers:
                self.params['maps'].pop(ntr)

        # Load dndz
        for tr in self.params['maps']:
            if tr['type'] in ['gc', 'wl']:
                fname = self.dndz+'/'+tr['dndz_file']
                tr['dndz'] = np.loadtxt(fname,unpack=True)

        # Load Covmat
        self.cov = np.zeros((len(self.data),len(self.data)))
        for ndv1 in range(self.n_data_vectors):
            dv1 = self.params['data_vectors'][ndv1]
            s1 = int(np.array([self.params['data_vectors'][x]['mask'] for x in range(ndv1)]).sum())
            e1 = s1+dv1['mask'].sum()
            for ndv2 in range(ndv1,self.n_data_vectors):
                dv2 = self.params['data_vectors'][ndv2]
                s2 = int(np.array([self.params['data_vectors'][x]['mask'] for x in range(ndv2)]).sum())
                e2 = s2+dv2['mask'].sum()
                fname = find_file_cov(self.cov_cls,dv1['tracers'],dv2['tracers'])
                cov = np.load(fname)['arr_0']
                cov = cov[dv1['mask'],:][:,dv2['mask']]
                self.cov[s1:e1,s2:e2] = cov
                self.cov[s2:e2,s1:e1] = cov.T

        # Invert covariance matrix
        self.icov = np.linalg.inv(self.cov)

        # Print vector size and dof
        npars = len(data.get_mcmc_parameters(['varying']))
        vecsize = self.cov.shape[0]
        self.dof = vecsize - npars
        print('    -> Varied parameters = {}'.format(npars))
        print('    -> cl_cross_corr data vector size = {}'.format(vecsize))
        print('    -> cl_cross_corr dof = {}'.format(self.dof))

        np.savez_compressed(os.path.join(self.outdir, 'cl_cross_corr_data_info.npz'), cov=self.cov,
                            ells=self.ells_tosave, cls=self.data, tracers=self.tracers_tosave, dof=self.dof)
        # end of initialization


    def get_loggaussprior(self, value, name):
        center = '{}_prior_center'.format(name)
        var = '{}_prior_variance'.format(name)
        lp = -(value-eval('self.'+center))**2./2./eval('self.'+var)**2.
        return lp


    # compute likelihood

    def loglkl(self, cosmo, data):

        # Initialize logprior
        lp = 0.

        # Get Tracers
        for tr in self.params['maps']:
            if tr['type'] == 'gc':
                # Import z, pz
                z  = tr['dndz'][1]
                pz = tr['dndz'][3]
                # Calculate z bias
                pname = 'gc_dz_{}'.format(tr['bin'])
                dz = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                z_dz = z-dz
                # Get log prior for dz
                lp = lp + self.get_loggaussprior(dz, pname)
                # Set to 0 points where z_dz < 0:
                sel = z_dz >= 0
                z_dz = z_dz[sel]
                pz = pz[sel]
                # Calculate bias
                pname = 'gc_b_{}'.format(tr['bin'])
                bias = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                bz = bias*np.ones(z.shape)
                # Get tracer
                tr['tracer'] = ccl.NumberCountsTracer(cosmo.cosmo_ccl,has_rsd=False,dndz=(z_dz,pz),bias=(z,bz))
            elif tr['type'] == 'wl':
                # Import z, pz
                z  = tr['dndz'][1]
                pz = tr['dndz'][3]
                # Calculate z bias
                pname = 'wl_dz_{}'.format(tr['bin'])
                dz = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                z_dz = z-dz
                # Get log prior for dz
                lp = lp + self.get_loggaussprior(dz, pname)
                # Get log prior for m
                pname = 'wl_m_{}'.format(tr['bin'])
                value = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                lp = lp + self.get_loggaussprior(value, pname)
                # Set to 0 points where z_dz < 0:
                sel = z_dz >= 0
                z_dz = z_dz[sel]
                pz = pz[sel]
                # Calculate bias IA
                A = data.mcmc_parameters['wl_ia_A']['current']*data.mcmc_parameters['wl_ia_A']['scale']
                eta = data.mcmc_parameters['wl_ia_eta']['current']*data.mcmc_parameters['wl_ia_eta']['scale']
                z0 = data.mcmc_parameters['wl_ia_z0']['current']*data.mcmc_parameters['wl_ia_z0']['scale']
                bz = A*((1.+z)/(1.+z0))**eta*0.0139/0.013872474  # pyccl2 -> has already the factor inside. Only needed bz
                fz = np.ones(z.shape)
                # Get tracer
                tr['tracer'] = ccl.WeakLensingTracer(cosmo.cosmo_ccl,dndz=(z_dz,pz),ia_bias=(z,bz)) #,red_frac=(z,fz))
            elif tr['type'] == 'cv':
                tr['tracer'] = ccl.CMBLensingTracer(cosmo.cosmo_ccl, z_source=1100)#TODO: correct z_source
            else:
                raise ValueError('Type of tracer not recognized. It can be gc, wl or cv!')

        # Get theory Cls
        theory = np.array([])
        for ndv in range(self.n_data_vectors):
            dv = self.params['data_vectors'][ndv]
            tracer1 = next(x['tracer'] for x in self.params['maps'] if x['name']==dv['tracers'][0])
            tracer2 = next(x['tracer'] for x in self.params['maps'] if x['name']==dv['tracers'][1])
            cls = ccl.angular_cl(cosmo.cosmo_ccl, tracer1, tracer2, dv['ells'])
            # Add multiplicative bias to WL
            type1 = next(x['type'] for x in self.params['maps'] if x['name']==dv['tracers'][0])
            type2 = next(x['type'] for x in self.params['maps'] if x['name']==dv['tracers'][1])
            if type1 == 'wl':
                bin = next(x['bin'] for x in self.params['maps'] if x['name']==dv['tracers'][0])
                pname = 'wl_m_{}'.format(bin)
                m = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                cls = (1.+m)*cls
            if type2 == 'wl':
                bin = next(x['bin'] for x in self.params['maps'] if x['name']==dv['tracers'][1])
                pname = 'wl_m_{}'.format(bin)
                m = data.mcmc_parameters[pname]['current']*data.mcmc_parameters[pname]['scale']
                cls = (1.+m)*cls
            theory = np.append(theory,cls)

        # Get chi2
        chi2 = (self.data-theory).dot(self.icov).dot(self.data-theory)

        lkl = lp - 0.5 * chi2

        # np.savez_compressed(os.path.join(self.outdir, 'cl_cross_corr_bestfit_info.npz'), chi2=2*lkl, chi2dof=2*lkl/self.dof,
        #                     cls=theory, ells=self.ells_tosave, tracers=self.tracers_tosave)


        return lkl
