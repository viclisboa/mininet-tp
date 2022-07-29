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
        self.addHost( 'h1', cpu=cpu )
        self.addHost( 'h2', cpu=cpu )

        # Here I have created a switch.  If you change its name, its
        # interface names will change from s0-eth1 to newname-eth1.
        switch = self.addSwitch('s0')
    
        self.addLink('h1', 's0', bw=1000,
                      max_queue_size=100 )
        self.addLink('h2', 's0', bw=1.5,
                      max_queue_size=100 )              

# Simple wrappers around monitoring utilities.  You are welcome to
# contribute neatly written (using classes) monitoring scripts for
# Mininet!

def start_iperf(client_host, server_host):
    print("Starting iperf server...")
    # For those who are curious about the -w 16m parameter, it ensures
    # that the TCP flow is not receiver window limited.  If it is,
    # there is a chance that the router buffer may not get filled up.
    server_command = "iperf -s -w 16m"
    client_command = f"iperf -c {server_host.IP()} -p 5001 -t 3600 -i 1 -w 16m -Z reno"

    print(f"  {server_host.name}: {server_command}")
    print(f"  {client_host.name}: {client_command}")

    server = server_host.popen(server_command)
    client = client_host.popen(client_command)

def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

def start_ping(source_host, target_host, dir="./"):
    interval = 0.1
    count = int(args.time / interval)
    command = f"ping -c {count} -i {interval} {target_host.IP()} > {dir}/ping.txt"
    print("Starting ping...")
    print(f"  {source_host.name}: {command}")
    source_host.popen(command, shell=True)

def start_webserver(host):
    command = 'cd ./http/; nohup python3 ./webserver3.py &'
    print("Starting webserver...")
    print(f"  {host.name}: {command}")
    host.cmd(command)
    sleep(1)

def bufferbloat():
    os.system("mn -c")
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)
    os.system("sysctl -w net.ipv4.tcp_congestion_control=%s" % args.cong)
    topo = BBTopo(maxq=args.maxq)
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink)
    net.start()
    # This dumps the topology and how nodes are interconnected through
    # links.
    dumpNodeConnections(net.hosts)
    # This performs a basic all pairs ping test.
    net.pingAll()

    h1 = net.get('h1')
    h2 = net.get('h2')
    print("h1 IP:", h1.IP())
    print("h2 IP:", h2.IP())

    # TODO: Start monitoring the queue sizes.  Since the switch I
    # created is "s0", I monitor one of the interfaces.  Which
    # interface?  The interface numbering starts with 1 and increases.
    # Depending on the order you add links to your network, this
    # number may be 1 or 2.  Ensure you use the correct number.
    #s0-eth2
    qmon = start_qmon(iface='s0-eth2',
                      outfile='%s/q.txt' % (args.dir))

    # TODO: Start iperf, webservers, etc.
    start_webserver(host=h1)
    start_iperf(server_host=h2, client_host=h1)
    start_ping(source_host=h1, target_host=h2, dir=args.dir)

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
    while True:
        # do the measurement (say) 3 times.
        sleep(5)
        now = time()
        elapsed_time = now - start_time
        if elapsed_time >= args.time:
            break
        print("%.1fs left..." % (args.time - elapsed_time))
        h2.sendCmd('curl -o /dev/null -s -w %{time_total} 10.0.0.1')
        result = h2.waitOutput()
        print("Curl result:")
        print(result.strip())

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

if __name__ == "__main__":
    bufferbloat()
