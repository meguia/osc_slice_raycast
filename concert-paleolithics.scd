/////////////////////////////////////
//
// INTRODUCTION AND COLLECTING MATERIAL FOR THE CONCERT
// Rotational wave terrain synthesis
// Eguia/Rohrhuber Version 0.2
//
//
/////////////////////////////////////


// init start: we initialise functions in an environment in the variable q
// depending on the max datagram size of your system, the maximum size of the array may be limited
// on macos, you can set it by sudo sysctl -w net.inet.udp.maxdgram=65535

// IN A FUTURE VERSION; WE WILL HAVE VISUALISATION; THIS ONE IS STILL WITHOUT

(
q = ();
// you may have to set this path
q.blenderPath = "/Applications/Blender.app/Contents/MacOS/blender";
// initialise functions
"startup-functions.scd".resolveRelative.load;
)


/////////// Preparation //////////////


// start server
s.boot;

// different variants
//q.initRWS(1024, numChannels:4);
q.initRWS(1024, numChannels:16);
q.dataObjects.size; // => 4 or 16

// switch visuals on, if you like
q.visualsOn = true;

// start the python/blender part
q.initBlender; // if this fails, make sure your old blender instance isn't running

// construct a loop for each channel
(
Ndef.clear;
Ndef(\loop).reshaping = \elastic;
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\loopBufferOffsets, {
	var numObjects = q.dataObjects.size;
	\bufferOffsets.kr(0 ! numObjects, \lag.kr(0.01))
});
Ndef(\loop).set(\bufferOffsets, Ndef(\loopBufferOffsets));
Ndef(\loop, {
	var numSamples = q.numSamples;
	var numObjects = q.dataObjects.size;

	var freq = \freq.kr(20, 0.05);
	var ratios = \ratios.kr(1 ! numObjects);
	var phaseOffsets = \phaseOffsets.kr(0 ! numObjects);
	var indices = (0..numObjects-1);
	q.bufferUGenLoop(indices, freq * ratios, phaseOffsets);

}).scope;
)


// testing the visuals (only if you have switched them on)

(type:\rwts, show:1, objID:3, channel:0, normal:[1, 0, 0]).play;
(type:\rwts, show:1, objID:3, channel:0, normal:[0, 0.1, 1]).play;
(type:\rwts, show:1, objID:3, channel:0, normal:[0, 1, 1]).play;
(type:\rwts, show:1, objID:7, channel:0, normal:[0, 1, 1]).play;

(type:\rwts, show:1, objID:(7..9)+4, channel:(0..15), normal:[0, 0, 1]).play;

Ndef(\loop).set(\freq, Ndef(\scanFreq, 900));

(
Tdef(\x, {
	inf.do { |i|
		(type:\rwts, show:2.rand, objID:(0..15) + 4, channel:(0..15), normal:q.rotate([0, 1, 0], [i/20, 0, 0]), distance:0.2.rand2).play;
		0.02.wait
	}
}).play
);

(
Tdef(\x, {
	inf.do { |i|
		(type:\rwts, show:1, objID:(0..15) + 4, channel:(0..15), normal:q.rotate([0, 1, 0], [i/20, 0, 0]), distance:0).play;
		0.02.wait
	}
}).play
);


Tdef(\x).stop;

q.show((0..19), 0); // hide all objects

/////////// Diving into the rotations //////////////


// measure some loops, and put them on channels 0..3
// all channels should do something now

q.getLoop((0..3), (0..3), normal:[0, 1, 0], distance: 0);
q.getLoop((0..15), (0..15) + 3, normal:[0, 0, 1], distance: 0);


(type:\rwts, show:1, objID:(1..16), channel:(0..15), normal:[1, 1, 0]).play;


// for the first tests, it is best to use parameter mapping
// frequency modulation N -> stereo

// we use a global common freq for all channels that is then mapped to the loop freq
Ndef(\scanFreq, 0.4);

(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(rotData * 3000);
	Splay.ar(sine) * 0.1
}).play;
)

// you can adjust the distanece from the center at which the loop is cut
q.getLoop((0..3), (0..3), normal:[0, 1, 0], distance: 0.2);

// with an event type, we can intergrate this into the pattern system
(type:\rwts, channel:(0..3), objID:(0..3)+2, normal:[0, 1, 0], distance: 0.2).play;

// these events can be sequenced:
(
Tdef(\x, {
	(-1,-0.9..1).do { |dist|
		(type:\rwts, channel:(0..3), objID:(0..3)+2, normal:[0, 1, 0], distance: dist).play;
		0.2.wait;
	};
}).play
)

// dur this determines the speed at which we move along the axis
// if this is too short, we drop frames (or loops)
(
Ndef(\scanFreq, 2);
Pdef(\x,
	Pbind(
		type:\rwts,
		channel:(0..3),
		objID:(0..3)+2,
		normal:[0, 1, 0],
		distance: Pseries(0, 0.01, 80),
		dur:0.1
	)
).play;
)


// there is also a parameter which rotates the normal of the cut, using three angles
(
Ndef(\scanFreq, 2);
Pdef(\x,
	Pbind(
		type:\rwts,
		channel:(0..3),
		objID:1,
		normal:[0, 1, 0],
		rotations: [0, 0, 2pi] * Pseries(0, 0.01, inf),
		distance: 0,
		dur:0.1
	)
).play;
)

// four objects rotating together
(
Ndef(\scanFreq, 2);
Pdef(\x,
	Pbind(
		type:\rwts,
		channel:(0..3),
		objID:(0..3) + 2,
		normal:[0, 1, 0],
		rotations: [0, 0, 2pi] * Pseries(0, 0.01, inf),
		distance: 0,
		dur:0.01
	)
).play;
)

// try another sonification, with filtered impulses
// whose frequency is proportional to the current diameter
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var diameter = Ndef.kr(\diameters);
	var sine = BPF.ar(Blip.ar(10 * diameter, 100), rotData.lag(0.01) * 3000 + 100, 0.05) * 20;
	Normalizer.ar(Splay.ar(sine)) * 0.3
}).play;
)

Ndef(\scanFreq, 7);
Ndef(\scanFreq, 17);
Ndef(\scanFreq, 100);
Ndef(\scanFreq, 1000);


///////////////////////////////////////////////////////////////////////////////
// UNFINISHED BELOW THIS


(
Ndef(\x, {
	//var rotData = q.bufferUGenRun(0, MouseX.kr(10, 800, 1));
	var rotData = q.bufferUGenRun(0, 8);

	//Blip.ar(rotData.linexp(Ndef.kr(\min, 1), Ndef.kr(\max, 1), 6, 100));
	SinOsc.ar(rotData.linexp(Ndef.kr(\min, 1), Ndef.kr(\max, 1), 300, 8000)) * 0.1;
}).play;
)


// direct audification mono
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 110);
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var mid = Ndef.kr(\midRadius, 1);
	Normalizer.ar(rotData - mid, 0.5)
}).play;
)

// scale by diameter
Ndef(\loop).set(\ratios, Ndef(\diameters));


q.getLoop(0, 0, normal:[0, 1, 0], distance: 0);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.1);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.2);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.5);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.8);
q.getLoop(0, 0, normal:[0, 1, 0], distance: (0..3) * 0.2);

q.getLoop(0, 0, normal: q.rotate([0, 1, 0], [0.01pi, 0, 0]), distance: 0);
q.getLoop(0, 0, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: 0);

q.getLoopSingle(2, 1, normal:[0, 1, 0], distance: 0);
q.getLoopSingle(3, 1, normal:[0, 1, 0], distance: 0);
q.getLoop((0..4), (0..4), normal:[0, 1, 0], distance: 0);


q.getLoop((0..3), 0, normal:[0, 1, 0], distance: 0.1/[1, 2, 3, 4]);
q.getLoop((0..3), objID:-1);

q.getLoop(3, objID:(5..8)); ////////////
q.getLoop(0, objID:8);






q.getLoop(0, 2, normal:[0, 1, 0], distance: 0);
q.getLoop(1, 1, normal:[0, 1, 0], distance: 0);

q.dataObjects.at(0).segments.plot
q.dataObjects.at(0).distances.plot


q.oscFunc = { |self, data| data.postln };


// direct audification multichannel
(
Ndef(\scanFreq, 110);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var mid = Ndef.kr(\midRadius);
	Splay.ar(Normalizer.ar(rotData - mid, 0.5))
	//Normalizer.ar(rotData - mid, 0.5) * 0.1
}).play;
)

Ndef(\x).plot(0.07)

q.getLoop(0, 1, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: 0);
q.getLoop(1, 7, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: 0);
q.getLoop(2, 4, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: 0);
q.getLoop(3, 5, normal: q.rotate([0, 1, 0], [0, 0, 0]), distance: 0);




q.getLoop(0, 0, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: [0, 0.1, 0.2, 0.3]);

q.getLoop(0, 0, normal: q.rotate([0, 1, 0], [0.02pi, 0, 0]), distance: [0, 0, 0, 0.3]);


// frequency modulation N -> stereo
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 0.5);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(rotData * 1310);
	Splay.ar(sine) * 0.1
}).play;
)

(
Ndef(\loop).set(\ratios, Ndef(\diameters));
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 0.5);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sig = RLPF.ar(Blip.ar(rotData * 310, 40) * 7, rotData * 8000, 0.05);
	sig = LeakDC.ar(Normalizer.ar(sig));
	Splay.ar(sig)
}).play;
)


Pbindef(\x, type: \rwts, objID: 0, channel: 0, rotation: Pfunc { (0.1pi ! 3).rand2 }, dur: 0.1).play;

(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 0.5);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sig = RLPF.ar(Blip.ar(rotData.linexp(0, 0.4, 10, 380), 140) * 7, rotData * 8000, 0.05);
	sig = LeakDC.ar(Normalizer.ar(sig));
	Splay.ar(sig)
}).play;
)





// direct audification multichannel
(
Ndef(\scanFreq, 1);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var mid = Ndef.kr(\midRadius);
	Splay.ar(SinOsc.ar(rotData - mid * 1900 + 500)) * 0.2
}).play;
)

(
q.dataObjects.size.do { |i|
	q.getLoop(i, 11 + (i%3), normal: q.rotate([0, 1, 0], [i/16*pi + 0.3, 0, 0]), distance: 0);
};
)

(
q.dataObjects.size.do { |i|
	q.getLoop(i, 9, normal: q.rotate([0, 1, 0], [i/17*pi, 0, 0]), distance: -0.1);
};
)

// minimal GUI: rotation
(
Slider(bounds:Rect(200, 300, 500, 60))
.action_({ |sl|
	q.dataObjects.size.do { |i|
		var norm = q.rotate([0, 1, 0], [sl.value.linlin(0, 1, 0, 2pi), 0, 0]);
		q.getLoop(i, i, normal:norm);
	};
}).front
)


// minimal GUI
(
Slider(bounds:Rect(200, 300, 500, 60))
.action_({ |sl|
	q.dataObjects.size.do { |i|
		q.getLoop(i, i, normal:[0, 1, 0], distance: sl.value.linlin(0, 1, -1, 1));
	};
}).front
)


// direct waveform synthesis
(
Ndef(\scanFreq, 110);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var mid = Ndef(\midRadius).kr;
	Normalizer.ar(rotData - mid) * mid
}).play.scope;
)

// synchronous amplitude modulation
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 210);
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var sine = SinOsc.ar(Ndef.kr(\scanFreq));
	sine * rotData * 0.2
}).play.scope;
)

// synchronous amplitude modulation II
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 210);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(Ndef.kr(\scanFreq));
	var mid = Ndef(\midRadius).kr;
	(sine * mid) + (rotData - mid) * 0.2
}).play.scope;
)

// asynchronous amplitude modulation
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 3);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(400);
	Splay.ar(sine * rotData) * 0.9
}).play.scope;
)

q.getLoop(0, 0, normal:[0, 1, 0], distance: 0);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.02);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.2);

// frequency modulation
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 0.3);
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var sine = SinOsc.ar(300 + (rotData * 350));
	Splay.ar(sine) * 0.2
}).play;
)

// frequency modulation N -> stereo
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 0.3);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(300 + (rotData * 310));
	Splay.ar(sine) * 0.1
}).play;
)

// frequency modulation 16
(
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\scanFreq, 3);
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var sine = SinOsc.ar(300 + (rotData * 310));
	sine * 0.1
}).play;
)


Ndef(\scanFreq, 0.3)



// frequency modulation rescaled
(
Ndef(\scanFreq, 0.3);
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var mod = rotData.linlin(Ndef.kr(\min), Ndef.kr(\max), 200, 800);
	var sine = SinOsc.ar(mod);
	sine * 0.2
}).play;
)
// frequency modulation rescaled 16

(
//Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var mod = rotData.linlin(Ndef.kr(\min), Ndef.kr(\max), 200, 800);
	var sine = SinOsc.ar(mod);
	sine * 0.2
}).play;
)



// frequency modulation rescaled
(
Ndef(\scanFreq, { MouseX.kr(2, 600, 1) });
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var mod = rotData.linlin(Ndef.kr(\min), Ndef.kr(\max), 20, 80);
	var sine = Blip.ar(mod, 40);
	sine * 0.2
}).play;
)

// frequency modulation rescaled 16
(
Ndef(\scanFreq, { MouseX.kr(0.2, 600, 1) });
Ndef(\loop).set(\freq, Ndef(\scanFreq));
Ndef(\x, {
	var rotData = Ndef.ar(\loop);
	var mod = rotData.linlin(Ndef.kr(\min), Ndef.kr(\max), 200, 480);
	var sine = Blip.ar(mod, 4);
	sine * 0.2
}).play;
)


q.getLoop(0, 0, normal:[1, 0, 0], distance: 0.04);
q.getLoop(0, 0, normal:[1, 0, 0], distance: 0.03);
q.getLoop(0, 0, normal:[0, 1, 0], distance: 0.02);
q.getLoop(0, 0, normal:[0, 0.8, 0], distance: 0.01);

// sweep
(
Tdef(\x, {
	(0..100).do { |i|
		q.getLoop(0, 0, normal:[1, 0, 0], distance: i.linlin(0, 100, -0.1, 0.1));
		0.1.wait;

	};
}).play
)

// second rotation axis

q.matrixmul([0, 1, 0], q.makeRotationMatrix([0.5pi, 0, 0]));

q.rotate([0, 1, 0], [0.5pi, 0, 0]);

// test full circle
(
a = (0..30).collect { |i|
	var angle = i.linlin(0, 30, 0, 2pi);
	q.rotate([0, 1, 0], [angle, 0, 0]);
}.flop.plot
)

// rotate scan
(
var id = 4;
Tdef(\x, {
	var n = 100;
	var dt = 0.03;
	Ndef(\loop).set(\lag, dt);
	inf.do { |i|
		var angle = (i % n).linlin(0, n, 0, 2pi);
		var normal = q.rotate([0, 1, 0], [angle, 0, 0]);
		q.getLoop(0, id, normal:normal, distance: 0);
		dt.wait;
		if(i % n == 0) { "%. loop completed".format(i div: n).postln };
	};
}).play
)

// rotate all scans
(
var id = 4;
Tdef(\x, {
	var n = 100;
	var dt = 0.1;
	Ndef(\loop).set(\lag, dt);
	inf.do { |i|
		var angle = (i % n).linlin(0, n, 0, 2pi);
		var normal = q.rotate([0, 1, 0], [angle, 0, 0]);
		q.dataObjects.size.do { |i|
			q.getLoop(i, id + i, normal:normal, distance: 0);
		};
		dt.wait;
		if(i % n == 0) { "%. loop completed".format(i div: n).postln };
	};
}).play
)

Ndef(\scanFreq, 67);

NdefMixer(s)

(
Ndef(\scanFreq, 3);
Ndef(\x, {
	var rotData = Ndef.ar(\loop, 1);
	var mod = rotData * 1900;
	var sine = SinOsc.ar(200 + mod);
	sine * 0.2
}).play;
)
Ndef(\scanFreq, { MouseX.kr(0.1, 90, 1, 0.5) });


Tdef(\x).stop;


// rotate scan, each object k times
(
var k = 2;
var nObjects = 20;
Tdef(\x, {
	var n = 100;
	var dt = 0.03;
	Ndef(\loop).set(\lag, dt);
	inf.do { |id|
		(k * n).do { |i|
			var angle = (i % n).linlin(0, n, 0, 2pi);
			var normal = q.rotate([0, 1, 0], [angle, 0, 0]);
			q.getLoop(0, id % nObjects, normal:normal, distance: 0);
			dt.wait;
			if(i % n == 0) { "%. loop completed (object %)".format(i div: n, id % nObjects).postln };
		};
	}
}).play
)