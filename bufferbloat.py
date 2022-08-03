from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI

from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import sys
import os
import math

parser = ArgumentParser(description="Bufferbloat tests")
parser.add_argument('--bw-host', '-B',
                    type=float,
                    help="Bandwidth of host links (Mb/s)",
                    default=1000)

parser.add_argument('--bw-net', '-b',
                    type=float,
                    help="Bandwidth of bottleneck (network) link (Mb/s)",
                    required=True)

parser.add_argument('--delay',
                    type=float,
                    help="Link propagation delay (ms)",
                    required=True)

parser.add_argument('--dir', '-d',
                    help="Directory to store outputs",
                    required=True)

parser.add_argument('--time', '-t',
                    help="Duration (sec) to run the experiment",
                    type=int,
                    default=10)

parser.add_argument('--maxq',
                    type=int,
                    help="Max buffer size of network interface in packets",
                    default=100)

parser.add_argument('--buffer-size',
                    type=str,
                    help="Socket buffer size",
                    default="16m")

# Linux uses CUBIC-TCP by default that doesn't have the usual sawtooth
# behaviour.  For those who are curious, invoke this script with
# --cong cubic and see what happens...
# sysctl -a | grep cong should list some interesting parameters.
parser.add_argument('--cong',
                    help="Congestion control algorithm to use",
                    default="reno")

# Expt parameters
args = parser.parse_args()

class BBTopo(Topo):
    "Simple topology for bufferbloat experiment."

    def build(self, n=2, cpu=None, delay=10, maxq=None, diff=False):
        h1 = self.addHost( 'h1', cpu=cpu )
        h2 = self.addHost( 'h2', cpu=cpu )

        # Here I have created a switch.  If you change its name, its
        # interface names will change from s0-eth1 to newname-eth1.
        switch = self.addSwitch('s0')

        self.addLink(
            h1, switch,
            bw=1000,
            delay=f"{delay}ms",
            )
        self.addLink(
            switch,h2,
            bw=1.5,
            max_queue_size=maxq,
            delay=f"{delay}ms",
            )

# Simple wrappers around monitoring utilities.  You are welcome to
# contribute neatly written (using classes) monitoring scripts for
# Mininet!

def start_iperf(client_host, server_host, congestion_control):
    print("Starting iperf server...")
    # NOTA: buf_size será passado para o parametro -w, mencionado abaixo.
    # For those who are curious about the -w 16m parameter, it ensures
    # that the TCP flow is not receiver window limited.  If it is,
    # there is a chance that the router buffer may not get filled up.
    server_ip = server_host.IP()
    buf_size = args.buffer_size

    server_command = f"iperf -s -w 16m"
    client_command = f"iperf -c {server_ip} -t 3600"

    print(f"  {server_host.name}: {server_command}")
    print(f"  {client_host.name}: {client_command}")

    server = server_host.popen(server_command)
    # atrasar a iniciação do cliente para evitar erro
    # quando o servidor é iniciado após o cliente
    sleep(1)
    client = client_host.popen(client_command)

def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

def start_ping(source_host, target_host, dir="./"):
    interval = 0.1
    count = int(args.time / interval)
    command = f"ping -i {interval} {target_host.IP()} > {dir}/ping.txt"
    print("Starting ping...")
    print(f"  {source_host.name}: {command}")
    source_host.popen(command, shell=True)

def web_download(server_host,source_host,net):
    results = 0
    for i in range(3):
        t = source_host.popen('curl -o /dev/null -s -w %%{time_total} %s/index.html' % server_host.IP(),shell=True,text=True,stdout=PIPE)
        output = t.stdout.readline()
        results += float(output)
    return results 

def start_webserver(host):
    command = 'nohup python3 ./webserver3.py &'
    print("Starting webserver...")
    print(f"  {host.name}: {command}")
    host.cmd(command)
    sleep(1)

def print_args():
    d = args.__dict__
    print("Arguments:\n", "\n".join(f"    {a}={d[a]}" for a in d), sep="")

def bufferbloat():
    os.system("mn -c")
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)
    os.system("sysctl -w net.ipv4.tcp_congestion_control=%s" % args.cong)
    topo = BBTopo(maxq=args.maxq, delay=args.delay)
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()
    # This dumps the topology and how nodes are interconnected through
    # links.
    dumpNodeConnections(net.hosts)
    # This performs a basic all pairs ping test.
    net.pingAll()

    print_args()

    h1 = net.get('h1')
    h2 = net.get('h2')
    print("h1 IP:", h1.IP())
    print("h2 IP:", h2.IP())

    # TODO: Start monitoring the queue sizes.  Since the switch I
    # created is "s0", I monitor one of the interfaces.  Which
    # interface?  The interface numbering starts with 1 and increases.
    # Depending on the order you add links to your network, this
    # number may be 1 or 2.  Ensure you use the correct number.
    qmon = start_qmon(iface='s0-eth2',
                      outfile='%s/q.txt' % (args.dir))

    # TODO: Start iperf, webservers, etc.
    start_iperf(server_host=h2, client_host=h1,congestion_control=args.cong)
    start_ping(source_host=h1, target_host=h2, dir=args.dir)
    start_webserver(host=h1)

    # TODO: measure the time it takes to complete webpage transfer
    # from h1 to h2 (say) 3 times.  Hint: check what the following
    # command does: curl -o /dev/null -s -w %{time_total} google.com
    # Now use the curl command to fetch webpage from the webserver you
    # spawned on host h1 (not from google!)
    # Hint: Verify the url by running your curl command without the
    # flags. The html webpage should be returned as the response.

    # Hint: have a separate function to do this and you may find the
    # loop below useful.
    start_time = time()
    web_download_time = []
    
    while True:
        web_download_time.append(web_download(h1,h2,net))
        sleep(5)
        now = time()
        elapsed_time = now - start_time
        if elapsed_time >= args.time:
            break
        print("%.1fs left..." % (args.time - elapsed_time))
    #    h2.sendCmd('curl -o /dev/null -s -w %{time_total} 10.0.0.1')
    #    result = h2.waitOutput()
    #    print("Curl result:")
    #    print(result.strip())

    mean = sum(web_download_time) / len(web_download_time)
    print("mean for queue size " + str(args.maxq) + ":" + str(mean))
    stdev = sum([((x - mean) ** 2) for x in web_download_time]) ** 0.5
    print("stdev for queue size " + str(args.maxq) + ":" + str(stdev))
    # TODO: compute average (and standard deviation) of the fetch
    # times.  You don't need to plot them.  Just note it in your
    # README and explain.

    # Hint: The command below invokes a CLI which you can use to
    # debug.  It allows you to run arbitrary commands inside your
    # emulated hosts h1 and h2.
    #CLI(net)

    qmon.terminate()
    net.stop()
    # Ensure that all processes you create within Mininet are killed.
    # Sometimes they require manual killing.
    Popen("pgrep -f webserver3.py | xargs kill -9", shell=True).wait()
    Popen("pkill -f -9 ping",shell=True).wait()

if __name__ == "__main__":
    bufferbloat()
