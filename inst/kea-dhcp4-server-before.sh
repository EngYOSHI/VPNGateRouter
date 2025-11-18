nicname=eth1
ip=172.16.0.254/24
ip link add veth_${nicname}_0 type veth peer name veth_${nicname}_1
ip link add br_${nicname} type bridge
ip link set veth_${nicname}_0 master br_${nicname}
ip link set ${nicname} master br_${nicname}
ip link set veth_${nicname}_0 up
ip link set veth_${nicname}_1 up
ip link set br_${nicname} up
ip addr add ${ip} dev br_${nicname}
ip link set ${nicname} promisc on
