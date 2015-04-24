from wsgiref.simple_server import make_server
from webob import Request, Response
import pdb
import logging
import requests
import json
import uuid

LOG = logging.getLogger(__name__)

class Router(object):  
    def __init__(self):  
        self.path_info = {}  
    def route(self, environ, start_response):

        if self.path_info.has_key(environ['PATH_INFO']):
            app = self.path_info[environ['PATH_INFO']]
            return app(environ, start_response)
        else:
            app = self.path_info['/']
            return app(environ, start_response)

    def __call__(self, path):  
        def wrapper(clz):  
            self.path_info[path] = clz()  
        return wrapper



router = Router()

KEYSTONE_URLS = [
                    #('http://localhost:5000/v2.0','http://localhost:5000/v3'),
                    ('http://9.123.137.89:5000/v2.0','http://9.123.137.89:5000/v3'),
                    ('http://9.123.137.28:5000/v2.0','http://9.123.137.28:5000/v3')
                ]

TOKENS_CACHE = {}
ENDPOINTS_CACHE = {}


@router('/v2.0/tokens')  
class Tokens(object):

    def __call__(self,environ, start_response):
        
        req_method = environ['REQUEST_METHOD'].lower()

        if(hasattr(self,'on_' + req_method)):
            return getattr(self,'on_' + req_method)(environ, start_response)
        
        raise AttributeError('not implemented method:' + req_method)

    def _removeTenantInPayload(self,payload):
        payload = json.loads(payload)
        if payload.has_key('auth'):
            auth = payload['auth']
            tns = ['tenantName','tenantId']
            for key in tns:
                if auth.has_key(key):
                    auth.pop(key)

        return json.dumps(payload)

    def on_post(self, environ, start_response):  
        req = Request(environ)

        data = None
        if req.content_length:
            # we are mixing the multiple keystone, so we don't have concrete tenant info yet
            data = self._removeTenantInPayload(req.body)

        req.headers.pop('Host')
        
        new_token = str(uuid.uuid4())
        
        regions = {}
        # minimal expire date
        expires = '2014-01-31T14:30:58Z'
        os_resp = None
        for index, (endpoint, v3) in enumerate(KEYSTONE_URLS):
            # start from  1
            regin_idx = index + 1
            os_resp = self._getOSResponse(endpoint,v3,data,dict(req.headers.items()))
            
            if os_resp.status_code == 200 :
                s = json.loads(os_resp.content)

                TOKENS_CACHE['region'+str(regin_idx)+ new_token] = s['access']['token']['id']

                for dp in s['access']['serviceCatalog']:
                    #suppose only one region now

                    # we may need to backup the original region name
                    dp['endpoints'][0]['region'] = 'region'+str(regin_idx)

                regions['region' + str(regin_idx)] = s

            if cmp(regions['region' + str(regin_idx)]['access']['token']['expires'],expires):
                expires = regions['region' + str(regin_idx)]['access']['token']['expires']

        

        body = {"access":{
                    "token":{"id":new_token,"expires":expires,"tenant":{"id":"fake"}},
                    "serviceCatalog":[
                        {"endpoints":[],"endpoints_links":[],"type":"compute","name":"nova"},
                        {"endpoints":[],"endpoints_links":[],"type":"network","name":"neutron"},
                        {"endpoints":[],"endpoints_links":[],"type":"volumev2","name":"cinder"},
                        {"endpoints":[],"endpoints_links":[],"type":"computev3","name":"nova"},
                        {"endpoints":[],"endpoints_links":[],"type":"s3","name":"s3"},
                        {"endpoints":[],"endpoints_links":[],"type":"image","name":"glance"},    
                        {"endpoints":[],"endpoints_links":[],"type":"volume","name":"cinder"},    
                        # todo: we need to provide our virtual keystone endpoint here                     
                        {"endpoints":[],"endpoints_links":[],"type":"identity","name":"keystone"}
                        #{"endpoints":[],"endpoints_links":[],"type":"ec2","name":"ec2"} 
                    ],"user":{"id":"fake"}}}
        
        _endpoints = {
                        "compute"   : body['access']['serviceCatalog'][0]['endpoints'],
                        "network"   : body['access']['serviceCatalog'][1]['endpoints'],
                        "volumev2"  : body['access']['serviceCatalog'][2]['endpoints'],
                        "computev3" : body['access']['serviceCatalog'][3]['endpoints'],
                        "s3"        : body['access']['serviceCatalog'][4]['endpoints'],
                        "image"     : body['access']['serviceCatalog'][5]['endpoints'],
                        "volume"    : body['access']['serviceCatalog'][6]['endpoints'],
                        "identity"  : body['access']['serviceCatalog'][7]['endpoints'],
                    }
        for region_name,value in regions.items():
            for dp in value['access']['serviceCatalog']:
                if _endpoints.has_key(dp['type']):
                    _id = str(uuid.uuid4()).replace('-','')
                    _ep = {
                        "adminURL"  :"http://localhost:8000/"+_id+"/admin",
                        "region"    : dp['endpoints'][0]['region'],
                        "internalURL":"http://localhost:8000/"+_id+"/internal",
                        "publicURL":"http://localhost:8000/"+_id+"/public",
                        "id"        :_id
                    }
                    
                    ENDPOINTS_CACHE[_id] = json.dumps(dp['endpoints'][0])

                    _endpoints[dp['type']].append(_ep)

            
        return self._response(environ,start_response,json.dumps(body))
   
    def _response(self,environ,start_response,os_resp):

        resp = Response()
        resp.status = 200
        resp.content_type = 'application/json'
        resp.content_length = len(os_resp)
        resp.body = os_resp
        # resp.status = os_resp.status_code
        # content_type = os_resp.headers.pop(
        #     'Content-Type', 'application/json').split(';', 1)[0]

        # # Hack for test_delete_image_blank_id test. Somehow text/html comes
        # # back as the content-type when it's supposed to be text/plain.
        # if content_type == 'text/html':
        #     content_type = 'text/plain; charset=UTF-8'
        # resp.content_type = content_type
        # resp.content_length = os_resp.headers.pop('Content-Length', 0)

        # #hop by hop headers
        # os_resp.headers.pop('connection')

        # resp.headers = dict(os_resp.headers.items())
        # resp.body = os_resp.content

        return resp(environ,start_response)

    def _getOSResponse(self,endpoint,v3,data,headers):
        
        os_resp = requests.request('POST',
                                   endpoint+"/tokens",
                                   data=data,
                                   headers=headers,
                                   stream=False)
        #
        # if os_resp.status_code = 200 ....
        #
        # we need to get the token and userid from the tokens response
        res = json.loads(os_resp.content)
        token = res['access']['token']['id']
        user = res['access']['user']['id']

        # and then retrive the projects with V3 token

        os_resp = requests.request('GET',
                                   v3+"/users/"+user+"/projects",
                                   headers={'X-Auth-Token':token})
        # project name
        tenant_name = json.loads(os_resp.content)['projects'][1]['name']

        data = json.loads(data)
        
        data['auth']['tenantName'] = tenant_name
        
        os_resp = requests.request('POST',
                                   endpoint+"/tokens",
                                   data=json.dumps(data),
                                   headers=headers,
                                   stream=False)
        return os_resp        

@router("/")
class OpenStackResponder(object):
    def __call__(self,environ, start_response):

        req_method = environ['REQUEST_METHOD'].lower()

        if(hasattr(self,'on_' + req_method)):
            return getattr(self,'on_' + req_method)(environ, start_response)
        
        raise AttributeError('not implemented method:' + req_method)

    def _extract_id_type_url(self,path_qs):
        relative_uri = path_qs.lstrip('/').split('/')
        
        _id = relative_uri[0]
        _url_type = relative_uri[1]
                
        relative_uri = '/' + '/'.join(relative_uri[2:])

        return _id,_url_type,relative_uri

    def _standard_responder(self, environ, start_response):
        req = Request(environ)

        data = None
        if (req.method == 'POST' or req.method == 'PUT'):
            if req.content_length:
                data = req.body
            else:
                req.headers.pop('Content-Length')
        else:
            req.headers.pop('Content-Length')

        _id, _url_type, relative_uri = self._extract_id_type_url(req.path_qs)


        ep = json.loads(ENDPOINTS_CACHE[_id])
        endpoint = ep[_url_type+'URL'] + relative_uri

        if req.headers['X-Auth-Token']:
            req.headers['X-Auth-Token'] = TOKENS_CACHE[ep['region']+req.headers['X-Auth-Token']]

        os_resp = requests.request(req.method,
                                   endpoint,
                                   data=data,
                                   headers=req.headers,
                                   stream=False)
        
        resp_content = os_resp.content.replace('localhost:8000','localhost:8000/'+_id+'/'+_url_type)

        resp = Response()
        resp.status = os_resp.status_code
        content_type = os_resp.headers.pop(
            'Content-Type', 'application/json').split(';', 1)[0]

        # Hack for test_delete_image_blank_id test. Somehow text/html comes
        # back as the content-type when it's supposed to be text/plain.
        if content_type == 'text/html':
            content_type = 'text/plain; charset=UTF-8'
        
        resp.content_type = content_type
        resp.content_length = len(resp_content) # os_resp.headers.pop('Content-Length', 0)
        
        #hop by hop headers
        hd = dict(os_resp.headers.items())
        if(hd.has_key('connection')):
            hd.pop('connection')
        hd['content-type'] = 'application/json'
        resp.headers = hd
        resp.body = resp_content    
        
        return resp(environ,start_response)

    on_get = _standard_responder
    on_post = _standard_responder
    on_put = _standard_responder
    on_delete = _standard_responder
    on_head = _standard_responder
    on_trace = _standard_responder
    on_patch = _standard_responder
    on_connect = _standard_responder
    on_options = _standard_responder

class application:
    def __init__(self,router):
        self.router = router
        
    
    def __call__(self,environ, start_response):

       return self.router.route(environ,start_response)
        



httpd = make_server('',8000,application(router))

print "Serving on port 8000..."

httpd.serve_forever()
