#!/usr/bin/env python3
"""
Paloaltonetworks Deploy_Jenkins_Hack_Demo.py

This software is provided without support, warranty, or guarantee.
Use at your own risk.
"""
'''
Outputs to file deployment_status

Contents of json dict

{"WebInDeploy": "Success", "WebInFWConf": "Success", "waf_conf": "Success"}

'''

import logging
import ssl
import urllib
import xml.etree.ElementTree as et
import xml
import time
import argparse
import json

from pandevice import firewall
from pandevice import updater
from  python_terraform import Terraform

gcontext = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)


#logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = logging.Formatter('%(levelname)-8s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)





def getApiKey(hostname,username, password):
    '''Generate the API key from username / password
    '''

    data = {
        'type' : 'keygen',
        'user' : username,
        'password' : password
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = "https://" + hostname + "/api"
    encoded_data = urllib.parse.urlencode(data).encode('utf-8')
    api_key = ""
    while (True):
        try:
            response = urllib.request.urlopen(url, data=encoded_data, context=ctx).read()
            api_key = xml.etree.ElementTree.XML(response)[0][0].text
        except urllib.error.URLError:
            logger.info("[INFO]: No response from FW. Wait 60 secs before retry")
            time.sleep(60)
            continue
        else:
            logger.info("FW Management plane is Responding so checking if Dataplane is ready")
            logger.debug("Response to get_api is {}".format(response))
            return api_key





def getFirewallStatus(fwMgtIP, api_key):
    """
    Gets the firewall status by sending the API request show chassis status.
    :param fwMgtIP:  IP Address of firewall interface to be probed
    :param api_key:  Panos API key
    """
    global gcontext

    cmd = urllib.request.Request("https://" + fwMgtIP + "/api/?type=op&cmd=<show><chassis-ready></chassis-ready></show>&key=" + api_key)
    # Send command to fw and see if it times out or we get a response
    logger.info("Sending command 'show chassis status' to firewall")
    try:
        response = urllib.request.urlopen(cmd, data=None, context=gcontext, timeout=5).read()

    except urllib.error.URLError:
        logger.debug("No response from FW. So maybe not up!")
        return 'no'
        #sleep and check again?
    else:
        logger.debug("Got response to 'show chassis status' {}".format(response))

    resp_header = et.fromstring(response)
    logger.debug('Response header is {}'.format(resp_header))

    if resp_header.tag != 'response':
        logger.debug("Did not get a valid 'response' string...maybe a timeout")
        return 'cmd_error'

    if resp_header.attrib['status'] == 'error':
        logger.debug("Got an error for the command")
        return 'cmd_error'

    if resp_header.attrib['status'] == 'success':
        # The fw responded with a successful command execution. So is it ready?
        for element in resp_header:
            if element.text.rstrip() == 'yes':
                logger.info("FW Chassis is ready to accept configuration and connections")
                return 'yes'
            else:
                logger.info("FW Chassis not ready, still waiting for dataplane")
                return 'almost'

def write_status_file(dict):

    out = json.dumps(dict)
    f = open("./deployment_status.json", "w")
    f.write(out)
    f.close()


def main(fwUsername,fwPasswd):

    albDns = ''
    nlbDns = ''
    fwMgt = ''

    # Set run_plan to TRUE is you wish to run terraform plan before apply
    run_plan = False
    deployment_status = {}
    kwargs = {"auto-approve": True }

    # Class Terraform uses subprocess and setting capture_output to True will capture output
    # capture_output = kwargs.pop('capture_output', True)
    #
    # if capture_output is True:
    #     stderr = subprocess.PIPE
    #     stdout = subprocess.PIPE
    # else:
    #     stderr = sys.stderr
    #     stdout = sys.stdout

    #
    # Build Infrastructure
    #

    tf = Terraform(working_dir='./WebInDeploy')

    if run_plan:
        tf.plan(capture_output=False)


    return_code1, stdout, stderr = tf.apply(capture_output=True,**kwargs)
    if return_code1 != 2:
        logger.info("WebInDeploy failed")
        deployment_status = {'WebInDeploy': 'Fail'}
        write_status_file(deployment_status)
        exit()
    else:
        deployment_status = {'WebInDeploy':'Success'}
        write_status_file(deployment_status)




    albDns = tf.output('ALB-DNS')
    fwMgt = tf.output('MGT-IP-FW-1')
    nlbDns = tf.output('NLB-DNS')
    # fwUsername = "admin"
    # fwPasswd = "PaloAlt0!123!!"
    fw_trust_ip = fwMgt



    logger.info("Got these values from output of first run\n\n")
    logger.info("ALB address is {}".format(albDns))
    logger.info("nlb address is {}".format(nlbDns))
    logger.info("Firewall Mgt address is {}".format(fwMgt))


    class FWNotUpException(Exception):
        pass
    err = 'no'
    api_key =''
    api_key = getApiKey(fw_trust_ip,fwUsername,fwPasswd)

    while True:
        err = getFirewallStatus(fw_trust_ip,api_key)
        if err == 'cmd_error':
            logger.info("Command error from fw ")
            #raise FWNotUpException('FW is not up!  Request Timeout')

        elif err == 'no':
            logger.info("FW is not up...yet")
            print("FW is not up...yet")
            time.sleep(60)
            continue
            #raise FWNotUpException('FW is not up!')
        elif err == 'yes':
            logger.info("[INFO]: FW is up")
            break

    fw = firewall.Firewall(hostname=fw_trust_ip,api_username=fwUsername,api_password=fwPasswd)
    logger.info("Updating firewall with latest content pack")
    updateHandle = updater.ContentUpdater(fw)

    updateHandle.download()

    logger.info("Waiting 3 minutes for content update to download")
    time.sleep(180)
    updateHandle.install()

    #
    # Configure Firewall
    #

    tf = Terraform(working_dir='./WebInFWConf')
    kwargs = {"auto-approve": True }

    logger.info("Applying addtional config to firewall")

    if run_plan:
        tf.plan(capture_output=False,var={'mgt-ipaddress-fw1':fwMgt, 'int-nlb-fqdn':nlbDns})

    return_code2, stdout, stderr = tf.apply(capture_output=False,var={'mgt-ipaddress-fw1':fwMgt, 'int-nlb-fqdn':nlbDns},**kwargs)

    if return_code2 != 2:
        logger.info("WebFWConfy failed")
        deployment_status.update({'WebFWConfy': 'Fail'})
        write_status_file(deployment_status)
        exit()
    else:
        deployment_status.update({'WebFWConfy': 'Success'})
        write_status_file(deployment_status)


    logger.info("Commit changes to firewall")

    fw.commit()

    #
    # Apply WAF Rules
    #

    tf = Terraform(working_dir='./waf_conf')
    kwargs = {"auto-approve": True }

    logger.info("Applying WAF config to App LB")

    if run_plan:
        tf.plan(capture_output=False,var={'alb_arn':nlbDns},**kwargs)

    return_code3, stdout, stderr = tf.apply(capture_output=False,var={'alb_arn':nlbDns},**kwargs)

    if return_code3 != 2:
        logger.info("waf_conf failed")
        deployment_status.update({'waf_conf': 'Fail'})
        write_status_file(deployment_status)
        exit()
    else:
        deployment_status.update({'waf_conf': 'Success'})
        write_status_file(deployment_status)

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Build Jenkins Exploit Demo')
    parser.add_argument('--username', type=str, help='Firewall management Username')
    parser.add_argument('--password', type=str, help='Firewall management Password')
    args = parser.parse_args()

    main(args.username, args.password)








