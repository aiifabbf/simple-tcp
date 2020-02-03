# do not import anything else from loss_socket besides LossyUDP
from lossy_socket import LossyUDP
# do not import anything else from socket except INADDR_ANY
from socket import INADDR_ANY

import struct
import threading
import concurrent.futures
import time
import hashlib

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

        self.pushBuffer = {} # key is sequence number, value is body bytes
        self.pushLocalSeek = 0 # I have written until
        self.pushRemoteSeek = 0 # remote has read until

        self.pullBuffer = {} # key is sequence number, value is body bytes
        self.pullLocalSeek = 0 # I have read until
        self.pullRemoteSeek = 0 # remote has written until

        self.maxInFlightSegmentCount = 20

        self.lock = threading.Lock()

        self.closed = False

        self.executor = concurrent.futures.ThreadPoolExecutor()

        self.outBoundJob = self.executor.submit(self.outBoundWorker)
        self.inBoundJob = self.executor.submit(self.inBoundWorker)

    def send(self, data_bytes: bytes) -> None:
        """Note that data_bytes can be larger than one packet."""
        # Your code goes here!  The code below should be changed!

        # for now I'm just sending the raw application-level data in one UDP payload
        segmentSize = 1472
        headerSize = 16
        bodySize = segmentSize - headerSize

        for i in range(0, len(data_bytes), bodySize):
            body = data_bytes[i: i + bodySize]

            with self.lock:
                self.sendSegment(self.pushLocalSeek, self.pullLocalSeek, body)
                self.pushBuffer[self.pushLocalSeek] = body
                self.pushLocalSeek += len(body)

    def outBoundWorker(self) -> None:

        while not self.closed:
            time.sleep(0.1)

            with self.lock:
                if self.pushRemoteSeek < self.pushLocalSeek: # receiver did not acked every segments we sent

                    for k in sorted(self.pushBuffer.keys())[: self.maxInFlightSegmentCount]: # resend first 10 in the push buffer
                        v = self.pushBuffer[k]
                        if k >= self.pushRemoteSeek:
                            self.sendSegment(k, self.pullLocalSeek, v)

                else:
                    self.sendAck(self.pushLocalSeek, self.pullLocalSeek)

                # print("pull local seek:", self.pullLocalSeek)
                # print("pull remote seek:", self.pullRemoteSeek)
                # print("push local seek:", self.pushLocalSeek)
                # print("push remote seek:", self.pushRemoteSeek)

    def inBoundWorker(self) -> None: # background worker

        while not self.closed:
            if self.recvIntoBuffer():
                continue
            else:
                break

    def recvIntoBuffer(self) -> bool: # just receive data, update self.ack and send buffer
        data, addr = self.socket.recvfrom() # decode segment
        self.lastMessageTimestamp = time.time()

        if not data:
            return False

        self.lock.acquire()

        try:
            decoded = self.decodeSegment(data)
        except ValueError:
            # print("corrupted.")
            self.sendAck(self.pushLocalSeek, self.pullLocalSeek)
            self.lock.release()
            return True

        seq = decoded["seq"]
        ack = decoded["ack"]
        control = decoded["control"]
        body = decoded["body"]
        # print(">", seq, ack, control, body)

        self.pullRemoteSeek = max(self.pullRemoteSeek, seq)
        self.pushRemoteSeek = max(self.pushRemoteSeek, ack)

        if body:
            self.pullBuffer[seq] = body

            if self.pullLocalSeek == self.pullRemoteSeek:
                self.sendAck(self.pushLocalSeek, self.pullLocalSeek)
            else:

                seek = self.pullLocalSeek

                while True:
                    if seek not in self.pullBuffer:
                        break
                    else:
                        seek += len(self.pullBuffer[seek])

                self.pullLocalSeek = seek
                self.sendAck(self.pushLocalSeek, self.pullLocalSeek)
        else: # no data
            pass

        toPop = []

        for k, v in self.pushBuffer.items():
            if k + len(v) <= self.pushRemoteSeek:
                toPop.append(k)

        for k in toPop:
            self.pushBuffer.pop(k)

        self.lock.release()

        return True

    def sendAck(self, seq: int, ack: int) -> None:
        self.sendSegment(seq, ack)

    def sendSegment(self, seq: int, ack: int, body: bytes=b"") -> None:
        header = struct.pack(">ll", seq, ack)
        segment = header + body

        hasher = hashlib.sha1()
        hasher.update(segment)
        checksum = hasher.digest()[: 8]

        segment = header + checksum + body
        # print("<", seq, ack, checksum, body)
        self.socket.sendto(segment, (self.dst_ip, self.dst_port))

    def decodeSegment(self, data: bytes=b"") -> dict:
        seqack = data[: 8]
        control = data[8: 16]
        body = data[16: ]

        hasher = hashlib.sha1()
        hasher.update(seqack)
        hasher.update(body)
        checksum = hasher.digest()[: 8]

        if checksum != control:
            raise ValueError("Corrupted")

        seq, ack = struct.unpack(">ll", seqack)

        return {
            "seq": seq,
            "ack": ack,
            "control": control,
            "body": body,
        }

    def recv(self) -> bytes:
        """Blocks (waits) if no data is ready to be read from the connection."""
        # your code goes here!  The code below should be changed!
        
        # this sample code just calls the recvfrom method on the LossySocket
        while True:
            if self.seek in self.pullBuffer: # if requested segment has already arrived
                res = self.pullBuffer.pop(self.seek)
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
        self.lock.acquire()
        if self.pushRemoteSeek < self.pushLocalSeek:
            self.lock.release()

            try:
                self.outBoundJob.result(3)
            except concurrent.futures.TimeoutError:
                pass

        else:
            self.lock.release()

        self.socket.stoprecv()
        self.closed = True

        # print("pull buffer size:", len(self.pullBuffer))
        # print("push buffer size:", len(self.pushBuffer))
        # print("---")
        # print("pull local seek:", self.pullLocalSeek)
        # print("pull remote seek:", self.pullRemoteSeek)
        # print("push local seek:", self.pushLocalSeek)
        # print("push remote seek:", self.pushRemoteSeek)