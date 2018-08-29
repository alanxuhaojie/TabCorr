import h5py
import numpy as np
from astropy.table import Table
from halotools.empirical_models import PrebuiltHodModelFactory, model_defaults
from halotools.mock_observables import return_xyz_formatted_array
from halotools.sim_manager import sim_defaults
from halotools.utils import crossmatch
from halotools.utils.table_utils import compute_conditional_percentiles


class TabCorr:

    def __init__(self):
        self.init = False

    @classmethod
    def tabulate(cls, halocat, tpcf, *tpcf_args,
                 mode='auto',
                 Num_ptcl_requirement=sim_defaults.Num_ptcl_requirement,
                 cosmology=sim_defaults.default_cosmology,
                 prim_haloprop_key=model_defaults.prim_haloprop_key,
                 sec_haloprop=False,
                 sec_haloprop_key=model_defaults.sec_haloprop_key,
                 sec_haloprop_split=0.5, prim_haloprop_bins=100,
                 sats_per_prim_haloprop=3e-13, downsample=1.0,
                 verbose=False, redshift_space_distortions=True,
                 **tpcf_kwargs):
        r"""
        Tabulates correlation functions for halos such that galaxy correlation
        functions can be calculated rapidly.

        Parameters
        ----------
        halocat : object
            Either an instance of `~halotools.sim_manager.CachedHaloCatalog` or
            `~halotools.sim_manager.UserSuppliedHaloCatalog`. This halo catalog
            is used to tabubulate correlation functions.

        tpcf : function
            The halotools correlation function for which values are tabulated.
            Positional arguments should be passed after this function.
            Additional keyword arguments for the correlation function are also
            passed through this function.

        *tpcf_args : tuple, optional
            Positional arguments passed to the ``tpcf`` function.

        mode : string, optional
            String describing whether an auto- ('auto') or a cross-correlation
            ('cross') function is going to be tabulated.

        Num_ptcl_requirement : int, optional
            Requirement on the number of dark matter particles in the halo
            catalog. The column defined by the ``prim_haloprop_key`` string
            will have a cut placed on it: all halos with
            halocat.halo_table[prim_haloprop_key] <
            Num_ptcl_requirement*halocat.particle_mass will be thrown out
            immediately after reading the original halo catalog in memory.
            Default value is set in
            `~halotools.sim_defaults.Num_ptcl_requirement`.

        cosmology : object, optional
            Instance of an astropy `~astropy.cosmology`. Default cosmology is
            set in `~halotools.sim_manager.sim_defaults`. This might be used to
            calculate phase-space distributions and redshift space distortions.

        prim_haloprop_key : string, optional
            String giving the column name of the primary halo property
            governing the occupation statistics of gal_type galaxies. Default
            value is specified in the model_defaults module.

        sec_haloprop : boolean, optional
            Boolean determining whether halo correlation functions will be
            split by secondary halo properties. Note that doing so will not
            affect predictions for models that only rely on a primary halo
            property.

        sec_haloprop_key : string, optional
            String giving the column name of the secondary halo property
            governing the assembly bias. Must be a key in the table passed to
            the methods of `HeavisideAssembiasComponent`. Default value is
            specified in the `~halotools.empirical_models.model_defaults`
            module.

        sec_haloprop_split : float, optional
            Fraction between 0 and 1 defining how we split halos into two
            groupings based on their conditional secondary percentiles. Default
            is 0.5 for a constant 50/50 split.

        prim_haloprop_bins : int, optional
            Integer determining how many (logarithmic) bins in primary halo
            property will be used.

        sats_per_prim_haloprop : float, optional
            Float determing how many satellites sample each halo. For each
            halo, the number is drawn from a Poisson distribution with an
            expectation value of ``sats_per_prim_haloprop`` times the primary
            halo property.

        downsample : float, optional
            Fraction between 0 and 1 used to downsample the total sample used
            to tabulate correlation functions. Values below unity can be used
            to reduce the computation time. It should not result in biases but
            the resulting correlation functions will be less accurate.

        verbose : boolean, optional
            Boolean determing whether the progress should be displayed.

        redshift_space_distortions : boolean, optional
            Boolean determining whether redshift space distortions should be
            applied to halos/galaxies.

        *tpcf_kwargs : dict, optional
                Keyword arguments passed to the ``tpcf`` function.

        Returns
        -------
        halotab : TabCorr
            Object containing all necessary information to calculate
            correlation functions for arbitrary galaxy models.
        """

        halotab = cls()

        # First, we tabulate the halo number densities.
        halos = halocat.halo_table
        halos = halos[halos['halo_pid'] == -1]
        halos = halos[halos[prim_haloprop_key] >= Num_ptcl_requirement *
                      halocat.particle_mass]
        halos[sec_haloprop_key + '_percentile'] = (
            compute_conditional_percentiles(
                table=halos, prim_haloprop_key=prim_haloprop_key,
                sec_haloprop_key=sec_haloprop_key))

        halotab.gal_type = Table()
        n_h, log_prim_haloprop_bins = np.histogram(
            np.log10(halos[prim_haloprop_key]), bins=prim_haloprop_bins)
        prim_haloprop_bins = 10**log_prim_haloprop_bins
        if sec_haloprop:
            mask = (
                halos[sec_haloprop_key + '_percentile'] < sec_haloprop_split)
            n_h_0, log_prim_haloprop_bins = np.histogram(
                np.log10(halos[prim_haloprop_key][mask]),
                bins=log_prim_haloprop_bins)
            mask = (
                halos[sec_haloprop_key + '_percentile'] >= sec_haloprop_split)
            n_h_1, log_prim_haloprop_bins = np.histogram(
                np.log10(halos[prim_haloprop_key][mask]),
                bins=log_prim_haloprop_bins)
            halotab.gal_type['n_h'] = (
                np.tile(np.concatenate((n_h_0, n_h_1)), 2) /
                np.prod(halocat.Lbox))
            halotab.gal_type['sec'] = np.tile(
                np.repeat(np.array([0, 1]), len(n_h)), 2)
        else:
            halotab.gal_type['n_h'] = np.tile(n_h, 2) / np.prod(halocat.Lbox)

        halotab.gal_type['gal_type'] = np.concatenate((
            np.repeat('centrals'.encode('utf8'),
                      len(halotab.gal_type) // 2),
            np.repeat('satellites'.encode('utf8'),
                      len(halotab.gal_type) // 2)))
        halotab.gal_type['log_prim_haloprop_min'] = np.tile(
            log_prim_haloprop_bins[:-1], len(halotab.gal_type) //
            len(log_prim_haloprop_bins[:-1]))
        halotab.gal_type['log_prim_haloprop_max'] = np.tile(
            log_prim_haloprop_bins[1:], len(halotab.gal_type) //
            len(log_prim_haloprop_bins[1:]))
        halotab.gal_type['prim_haloprop'] = 10**(0.5 * (
            halotab.gal_type['log_prim_haloprop_min'] +
            halotab.gal_type['log_prim_haloprop_max']))

        # Now, we tabulate the correlation functions.
        model = PrebuiltHodModelFactory('zheng07', redshift=halocat.redshift,
                                        prim_haloprop_key=prim_haloprop_key)
        model.param_dict['logMmin'] = 0
        model.param_dict['sigma_logM'] = 0.1
        model.param_dict['alpha'] = 1.0
        model.param_dict['logM0'] = 0
        model.param_dict['logM1'] = - np.log10(sats_per_prim_haloprop)
        model.populate_mock(halocat, Num_ptcl_requirement=Num_ptcl_requirement)
        gals = model.mock.galaxy_table
        gals = gals[np.random.random(len(gals)) < downsample]

        idx_gals, idx_halos = crossmatch(gals['halo_id'], halos['halo_id'])
        assert np.all(gals['halo_id'][idx_gals] == halos['halo_id'][idx_halos])
        gals[sec_haloprop_key + '_percentile'] = np.zeros(len(gals))
        gals[sec_haloprop_key + '_percentile'][idx_gals] = (
            halos[sec_haloprop_key + '_percentile'][idx_halos])

        pos_all = return_xyz_formatted_array(
            x=gals['x'], y=gals['y'], z=gals['z'],
            velocity=gals['vz'] if redshift_space_distortions else 0,
            velocity_distortion_dimension='z', period=halocat.Lbox,
            redshift=halocat.redshift, cosmology=cosmology)

        pos = []
        for i in range(len(halotab.gal_type)):

            mask = (
                (10**(halotab.gal_type['log_prim_haloprop_min'][i]) <
                 gals[prim_haloprop_key]) &
                (10**(halotab.gal_type['log_prim_haloprop_max'][i]) >=
                 gals[prim_haloprop_key]) &
                (halotab.gal_type['gal_type'][i] == gals['gal_type']))
            if sec_haloprop:
                if halotab.gal_type['sec'][i] == 0:
                    mask = mask & (gals[sec_haloprop_key + '_percentile'] <
                                   sec_haloprop_split)
                else:
                    mask = mask & (gals[sec_haloprop_key + '_percentile'] >=
                                   sec_haloprop_split)

            pos.append(pos_all[mask])

        for i in range(len(halotab.gal_type)):

            if verbose:
                print("row %d/%d" % (i + 1, len(halotab.gal_type)))

            if mode == 'auto':
                for k in range(i, len(halotab.gal_type)):
                    if len(pos[i]) * len(pos[k]) > 0:
                        xi = tpcf(
                            pos[i], *tpcf_args,
                            sample2=pos[k] if k != i else None,
                            do_auto=(i == k), do_cross=(not i == k),
                            **tpcf_kwargs)
                        if 'tpcf_matrix' not in locals():
                            tpcf_matrix = np.zeros(
                                (len(xi.ravel()), len(halotab.gal_type),
                                 len(halotab.gal_type)))
                            tpcf_shape = xi.shape
                        tpcf_matrix[:, i, k] = xi.ravel()
                        tpcf_matrix[:, k, i] = xi.ravel()

            elif mode == 'cross':
                if len(pos[i]) > 0:
                    xi = tpcf(
                        pos[i], *tpcf_args, **tpcf_kwargs)
                    if tpcf.__name__ == 'delta_sigma':
                        xi = xi[1]
                    if 'tpcf_matrix' not in locals():
                        tpcf_matrix = np.zeros(
                            (len(xi.ravel()), len(halotab.gal_type)))
                        tpcf_shape = xi.shape
                    tpcf_matrix[:, i] = xi.ravel()

        halotab.attrs = {}
        halotab.attrs['tpcf'] = tpcf.__name__
        halotab.attrs['mode'] = mode
        halotab.attrs['simname'] = halocat.simname
        halotab.attrs['redshift'] = halocat.redshift
        halotab.attrs['Num_ptcl_requirement'] = Num_ptcl_requirement
        halotab.attrs['prim_haloprop_key'] = prim_haloprop_key
        halotab.attrs['sec_haloprop'] = sec_haloprop
        halotab.attrs['sec_haloprop_key'] = sec_haloprop_key
        halotab.attrs['sec_haloprop_split'] = sec_haloprop_split

        halotab.tpcf_args = tpcf_args
        halotab.tpcf_kwargs = tpcf_kwargs
        halotab.tpcf_shape = tpcf_shape
        halotab.tpcf_matrix = tpcf_matrix

        halotab.init = True

        return halotab

    @classmethod
    def read(cls, fname):
        r"""
        Reads tabulated correlation functions from the disk.

        Parameters
        ----------
        fname : string
            Name of the file containing the tabulated correlation functions.

        Returns
        -------
        halotab : TabCorr
            Object containing all necessary information to calculate
            correlation functions for arbitrary galaxy models.
        """

        halotab = cls()

        fstream = h5py.File(fname, 'r')
        halotab.attrs = {}
        for key in fstream.attrs.keys():
            halotab.attrs[key] = fstream.attrs[key]

        halotab.tpcf_matrix = fstream['tpcf_matrix'].value
        halotab.tpcf_args = []
        for key in fstream['tpcf_args'].keys():
            halotab.tpcf_args.append(fstream['tpcf_args'][key].value)
        halotab.tpcf_args = tuple(halotab.tpcf_args)
        halotab.tpcf_kwargs = {}
        if 'tpcf_kwargs' in fstream:
            for key in fstream['tpcf_kwargs'].keys():
                halotab.tpcf_kwargs[key] = fstream['tpcf_kwargs'][key].value
        halotab.tpcf_shape = tuple(fstream['tpcf_shape'].value)
        fstream.close()

        halotab.gal_type = Table.read(fname, path='gal_type')

        halotab.init = True

        return halotab

    def write(self, fname):
        r"""
        Writes tabulated correlation functions to the disk.

        Parameters
        ----------
        fname : string
            Name of the file that is written.
        """

        fstream = h5py.File(fname, 'w-')

        keys = ['tpcf', 'mode', 'simname', 'redshift', 'Num_ptcl_requirement',
                'prim_haloprop_key', 'sec_haloprop', 'sec_haloprop_key',
                'sec_haloprop_split']
        for key in keys:
            fstream.attrs[key] = self.attrs[key]

        fstream['tpcf_matrix'] = self.tpcf_matrix
        for i, arg in enumerate(self.tpcf_args):
            fstream['tpcf_args/arg_%d' % i] = arg
        for key in self.tpcf_kwargs:
            fstream['tpcf_kwargs/' + key] = self.tpcf_kwargs[key]
        fstream['tpcf_shape'] = self.tpcf_shape
        fstream.close()

        self.gal_type.write(fname, path='gal_type', append=True)

    def predict(self, model):
        r"""
        Predicts the number density and correlation function for a certain
        model.

        Parameters
        ----------
        model : HodModelFactory
            Instance of ``halotools.empirical_models.HodModelFactory``
            describing the model for which predictions are made.

        Returns
        -------
        ngal : numpy.array
            Array containing the number densities for each galaxy type
            stored in self.gal_type. The total galaxy number density is the sum
            of all elements of this array.

        xi : numpy.array
            Array storing the prediction for the correlation function.
        """

        mean_occupation = np.zeros(len(self.gal_type))

        mask = self.gal_type['gal_type'] == 'centrals'
        mean_occupation[mask] = model.mean_occupation_centrals(
            prim_haloprop=self.gal_type['prim_haloprop'][mask],
            sec_haloprop_percentile=(self.gal_type['sec'][mask] if
                                     self.attrs['sec_haloprop'] else None))
        mean_occupation[~mask] = model.mean_occupation_satellites(
            prim_haloprop=self.gal_type['prim_haloprop'][~mask],
            sec_haloprop_percentile=(self.gal_type['sec'][~mask] if
                                     self.attrs['sec_haloprop'] else None))

        ngal = mean_occupation * self.gal_type['n_h'].data
        if self.attrs['mode'] == 'auto':
            xi = (np.sum(self.tpcf_matrix * np.outer(ngal, ngal),
                         axis=(1, 2)) /
                  np.sum(ngal)**2).reshape(self.tpcf_shape)
        elif self.attrs['mode'] == 'cross':
            xi = (np.sum(self.tpcf_matrix * ngal, axis=1) /
                  np.sum(ngal)).reshape(self.tpcf_shape)

        return ngal, xi