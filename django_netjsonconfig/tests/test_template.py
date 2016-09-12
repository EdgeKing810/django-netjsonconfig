from django.core.exceptions import ValidationError
from django.test import TestCase

from netjsonconfig import OpenWrt

from . import CreateTemplateMixin, CreateVpnMixin
from .. import settings as app_settings
from ..models import Config, Template


class TestTemplate(CreateTemplateMixin, CreateVpnMixin, TestCase):
    """
    tests for Template model
    """
    def test_str(self):
        t = Template(name='test', backend='netjsonconfig.OpenWrt')
        self.assertEqual(str(t), '[Generic-OpenWRT] test')

    def test_backend_class(self):
        t = Template(name='test', backend='netjsonconfig.OpenWrt')
        self.assertIs(t.backend_class, OpenWrt)

    def test_backend_instance(self):
        config = {'general': {'hostname': 'template'}}
        t = Template(name='test', backend='netjsonconfig.OpenWrt', config=config)
        self.assertIsInstance(t.backend_instance, OpenWrt)

    def test_validation(self):
        config = {'interfaces': {'invalid': True}}
        t = Template(name='test', backend='netjsonconfig.OpenWrt', config=config)
        # ensure django ValidationError is raised
        with self.assertRaises(ValidationError):
            t.full_clean()

    def test_config_status_modified_after_change(self):
        t = self._create_template()
        c = Config(name='test-status',
                   backend='netjsonconfig.OpenWrt',
                   config={'general': {}})
        c.full_clean()
        c.save()
        c.templates.add(t)
        c.status = 'running'
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.status, 'running')
        t.config['interfaces'][0]['name'] = 'eth1'
        t.full_clean()
        t.save()
        c.refresh_from_db()
        self.assertEqual(c.status, 'modified')

    def test_no_auto_hostname(self):
        t = self._create_template()
        self.assertNotIn('general', t.backend_instance.config)
        t.refresh_from_db()
        self.assertNotIn('general', t.config)

    def test_default_template(self):
        t = self._create_template(default=True)
        c = Config(name='test-default',
                   backend='netjsonconfig.OpenWrt')
        c.full_clean()
        c.save()
        self.assertEqual(c.templates.count(), 1)
        self.assertEqual(c.templates.first().id, t.id)

    def test_vpn_missing(self):
        try:
            self._create_template(type='vpn')
        except ValidationError as err:
            self.assertTrue('vpn' in err.message_dict)
        else:
            self.fail('ValidationError not raised')

    def test_generic_has_no_vpn(self):
        t = self._create_template(vpn=self._create_vpn())
        self.assertIsNone(t.vpn)
        self.assertFalse(t.auto_cert)

    def test_generic_has_create_cert_false(self):
        t = self._create_template()
        self.assertFalse(t.auto_cert)

    def test_create_cert(self):
        vpn = self._create_vpn()
        t = self._create_template(name='test-create-cert',
                                  type='vpn',
                                  vpn=vpn,
                                  auto_cert=True)
        c = Config(name='test-create-cert',
                   backend='netjsonconfig.OpenWrt',
                   config={'general': {}})
        c.full_clean()
        c.save()
        c.templates.add(t)
        c.save()
        vpnclient = c.vpnclient_set.first()
        self.assertIsNotNone(vpnclient)
        self.assertTrue(vpnclient.auto_cert)
        self.assertIsNotNone(vpnclient.cert)
        self.assertEqual(c.vpnclient_set.count(), 1)

    def test_automatically_created_cert_deleted_post_clear(self):
        self.test_create_cert()
        c = Config.objects.get(name='test-create-cert')
        vpnclient = c.vpnclient_set.first()
        cert = vpnclient.cert
        cert_model = cert.__class__
        c.templates.clear()
        self.assertEqual(c.vpnclient_set.count(), 0)
        self.assertEqual(cert_model.objects.filter(pk=cert.pk).count(), 0)

    def test_automatically_created_cert_deleted_post_remove(self):
        self.test_create_cert()
        c = Config.objects.get(name='test-create-cert')
        t = Template.objects.get(name='test-create-cert')
        vpnclient = c.vpnclient_set.first()
        cert = vpnclient.cert
        cert_model = cert.__class__
        c.templates.remove(t)
        self.assertEqual(c.vpnclient_set.count(), 0)
        self.assertEqual(cert_model.objects.filter(pk=cert.pk).count(), 0)

    def test_create_cert_false(self):
        vpn = self._create_vpn()
        t = self._create_template(type='vpn', auto_cert=False, vpn=vpn)
        c = Config(name='test-create-cert-false',
                   backend='netjsonconfig.OpenWrt',
                   config={'general': {}})
        c.full_clean()
        c.save()
        c.templates.add(t)
        c.save()
        vpnclient = c.vpnclient_set.first()
        self.assertIsNotNone(vpnclient)
        self.assertFalse(vpnclient.auto_cert)
        self.assertIsNone(vpnclient.cert)
        self.assertEqual(c.vpnclient_set.count(), 1)

    def test_auto_client_template(self):
        vpn = self._create_vpn()
        t = self._create_template(name='autoclient',
                                  type='vpn',
                                  auto_cert=True,
                                  vpn=vpn,
                                  config={})
        control = t.vpn.auto_client()
        self.assertDictEqual(t.config, control)

    def test_auto_client_template_auto_cert_False(self):
        vpn = self._create_vpn()
        t = self._create_template(name='autoclient',
                                  type='vpn',
                                  auto_cert=False,
                                  vpn=vpn,
                                  config={})
        vpn = t.config['openvpn'][0]
        self.assertEqual(vpn['cert'], 'cert.pem')
        self.assertEqual(vpn['key'], 'key.pem')
        self.assertEqual(len(t.config['files']), 1)
        self.assertIn('ca_path', t.config['files'][0]['path'])

    def _get_vpn_context(self):
        self.test_create_cert()
        c = Config.objects.get(name='test-create-cert')
        context = c.get_context()
        vpnclient = c.vpnclient_set.first()
        return context, vpnclient

    def test_vpn_context_ca_path(self):
        context, vpnclient = self._get_vpn_context()
        ca = vpnclient.cert.ca
        key = 'ca_path_{0}'.format(vpnclient.vpn.pk.hex)
        filename = 'ca-{0}-{1}.pem'.format(ca.pk, ca.common_name)
        value = '{0}/{1}'.format(app_settings.CERT_PATH, filename)
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_ca_contents(self):
        context, vpnclient = self._get_vpn_context()
        key = 'ca_contents_{0}'.format(vpnclient.vpn.pk.hex)
        value = vpnclient.cert.ca.certificate
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_cert_path(self):
        context, vpnclient = self._get_vpn_context()
        vpn_pk = vpnclient.vpn.pk.hex
        key = 'cert_path_{0}'.format(vpn_pk)
        filename = 'client-{0}.pem'.format(vpn_pk)
        value = '{0}/{1}'.format(app_settings.CERT_PATH, filename)
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_cert_contents(self):
        context, vpnclient = self._get_vpn_context()
        vpn_pk = vpnclient.vpn.pk.hex
        key = 'cert_contents_{0}'.format(vpn_pk)
        value = vpnclient.cert.certificate
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_key_path(self):
        context, vpnclient = self._get_vpn_context()
        vpn_pk = vpnclient.vpn.pk.hex
        key = 'key_path_{0}'.format(vpn_pk)
        filename = 'key-{0}.pem'.format(vpn_pk)
        value = '{0}/{1}'.format(app_settings.CERT_PATH, filename)
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_key_contents(self):
        context, vpnclient = self._get_vpn_context()
        vpn_pk = vpnclient.vpn.pk.hex
        key = 'key_contents_{0}'.format(vpn_pk)
        value = vpnclient.cert.private_key
        self.assertIn(key, context)
        self.assertIn(value, context[key])

    def test_vpn_context_no_cert(self):
        vpn = self._create_vpn()
        t = self._create_template(type='vpn', auto_cert=False, vpn=vpn)
        c = Config(name='test-create-cert-false',
                   backend='netjsonconfig.OpenWrt',
                   config={'general': {}})
        c.full_clean()
        c.save()
        c.templates.add(t)
        c.save()
        context = c.get_context()
        vpn_id = vpn.pk.hex
        cert_path_key = 'cert_path_{0}'.format(vpn_id)
        cert_contents_key = 'cert_contents_{0}'.format(vpn_id)
        key_path_key = 'key_path_{0}'.format(vpn_id)
        key_contents_key = 'key_contents_{0}'.format(vpn_id)
        ca_path_key = 'ca_path_{0}'.format(vpn_id)
        ca_contents_key = 'ca_contents_{0}'.format(vpn_id)
        self.assertNotIn(cert_path_key, context)
        self.assertNotIn(cert_contents_key, context)
        self.assertNotIn(key_path_key, context)
        self.assertNotIn(key_contents_key, context)
        self.assertIn(ca_path_key, context)
        self.assertIn(ca_contents_key, context)
