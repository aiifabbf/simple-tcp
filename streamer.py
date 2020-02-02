# do not import anything else from loss_socket besides LossyUDP
from lossy_socket import LossyUDP
# do not import anything else from socket except INADDR_ANY
from socket import INADDR_ANY

import struct

class Streamer:
    def __init__(self, dst_ip, dst_port,
                 src_ip=INADDR_ANY, src_port=0):
        """Default values listen on all network interfaces, chooses a random source port,
           and does not introduce any simulated packet loss."""
        self.socket = LossyUDP()
        self.socket.bind((src_ip, src_port))
        self.dst_ip = dst_ip
        self.dst_port = dst_port

        self.seek = 0 # expect to receive body from

        self.sendBuffer = {} # key is ack number, value is body bytes
        self.seq = 0 # sending body from

        self.receiveBuffer = {} # key is sequence number, value is body bytes
        self.ack = 0 # expecting body from

    def send(self, data_bytes: bytes) -> None:
        """Note that data_bytes can be larger than one packet."""
        # Your code goes here!  The code below should be changed!

        # for now I'm just sending the raw application-level data in one UDP payload
        segmentSize = 1472
        headerSize = 8
        bodySize = segmentSize - headerSize

        for i in range(0, len(data_bytes), bodySize):
            body = data_bytes[i: i + bodySize]
            header = struct.pack(">ll", self.seq, self.ack)
            segment = header + body
            self.socket.sendto(segment, (self.dst_ip, self.dst_port))
            self.seq += len(body)
            self.sendBuffer[self.seq] = body

            while True:
                self.recvIntoBuffer()
                if self.seq in self.sendBuffer:
                    self.retransmit(self.seq)
                else:
                    break

    def recvIntoBuffer(self) -> None:
        data, addr = self.socket.recvfrom()
        header = data[: 8]
        body = data[8: ]
        seq, ack = struct.unpack(">ll", header)
        if body: # a data + ack
            print("data segment", seq, ack)
            if seq == self.ack: # in order, normal situation
                self.receiveBuffer[seq] = body # put in buffer
                self.ack += len(body) # expecting next body
                self.sendAck(self.seq, self.ack)
            elif seq > self.ack: # out of order, latter comes first
                self.receiveBuffer[seq] = body # put in buffer

                seek = self.ack
                complete = True

                while seek != seq:
                    if seek in self.receiveBuffer:
                        seek += len(self.receiveBuffer[seek])
                    else:
                        complete = False
                        break

                if complete:
                    self.ack += len(body)
                    self.sendAck(self.seq, self.ack)
                else:
                    self.sendAck(self.seq, self.ack)
            else: # seq < self.ack
                self.sendAck(self.seq, self.ack)
        else: # an ack
            print("ack", seq, ack)
            pass

        if ack == self.seq: # receiver gets all, normal situation
            self.sendBuffer.clear()
        elif ack < self.seq: # receiver gets partial

            for k, v in self.sendBuffer:
                if k - len(v) >= ack:
                    self.sendSegment(k - len(v), self.ack, v)

        else: # ack > self.seq not possible
            pass

    def sendAck(self, seq: int, ack: int) -> None:
        self.sendSegment(seq, ack)

    def sendSegment(self, seq: int, ack: int, body: bytes=b"") -> None:
        print("sent", seq, ack, body)
        header = struct.pack(">ll", seq, ack)
        segment = header + body
        self.socket.sendto(segment, (self.dst_ip, self.dst_port))

    def recv(self) -> bytes:
        """Blocks (waits) if no data is ready to be read from the connection."""
        # your code goes here!  The code below should be changed!
        
        # this sample code just calls the recvfrom method on the LossySocket
        while True:
            if self.seek in self.receiveBuffer: # if requested segment has already arrived
                res = self.receiveBuffer.pop(self.seek)
                self.seek += len(res)
                break # feed body to upper layer immediately
            else: # if not arrived yet, wait
                self.recvIntoBuffer()

        return res
        # For now, I'll just pass the full UDP payload to the app

    def close(self) -> None:
        """Cleans up. It should block (wait) until the Streamer is done with all
           the necessary ACKs and retransmissions"""
        # your code goes here, especially after you add ACKs and retransmissions.
        pass
