# music-automation

This is a new project. It is aimed at producing a number of midi tracks that will serve as the foundation of a computer generated part of a song that will be later edited in Cubase.

The user will specify a tempo <tempo>, number of beats per bar <beats_per_bar>,number of time divisions per beat  <divisions_per_beat> (e.g., 16 means 16th notes), the number of bars in a probability or transition table cycle <bars_per_cycle>, and the base pitch for the generated tones <base_pitch>.
<division_per_bar> is divisions_per_beat*beats_per_bar.
<divisions_per_cycle> is then divisions_per_beat * beats_per_bar *bars_per_cycle.
There will be a set of probability and transition tables for:
1.	<note_probability_table> Vector of length divisions_per_beat * beats_per_bar *bars_per_cycle. Probability of a note starting on a particular beat location within the cycle (e.g., what is the probability of a note starting on a 16th note before the 3rd beat of the measure).
2.	<rest_length_table> Array of dimensions divisions_per_cycle by (divisions_per_bar-1). The probabilities of rest length as a function of beat location within the cycle.
3.	<note_length_table> Vector of length divisions_per_cycle by (divisions_per_bar-1). The probabilities of note length as a function of beat location within the cycle.
4.	<pitch_probability_table> Vector of length 12. A probability table giving the relative frequency of notes within the key. This is assumed to be cyclic and apply equally well to different octaves; pitch_probability_table[0] is the probability of being note base_pitch or octaves above or below base_pitch.
5.	<interval_probability_table> Vector of length 25. Probability table giving the relative frequency of different note intervals; interval_probability_table[0] is the probability of going down an octave; interval_probability_table[12] is the probability of no change; interval_probability_table[25] is the probability of going up an octave.
6.	<location_volume> is a vector of length divisions_per_cycle that gives the mean volume on a 0 to 1 scale.
7.	<volume_variability> is a real number indicated the standard deviation of the volume around that mean.
8.	<pitch_volume> is a vector of length 12 that multiplies location volume depending on note pitch. 
9.	<pitch_gravity> value indicating how likely the pitch transition table brings things back to the base pitch.  The maximum distance the note can get from base_pitch is 2 * pitch_gravity.

Algorithm for determining the next pitch. 
This may be made more complicated in the future, but we will start simply.

Assume
If the current pitch is <pitch_current>, we construct an <augmented_interval_probability_table, where augmented_interval_probability_table[i] = interval_probability_table[i] * pitch_probability_table[mod(i +pitch_current) – pitch].

We then apply a transformation to keep the notes close to the base pitch:
augmented_probability_table[i] = max(0, augmented_probability_table[i] * (1 – (i – 12 + pitch_current – pitch_base)/pitch_gravity)

If we then say  

augmented_interval_probability_table = augmented_interval_probability_table / np.sum(augmented_interval_probability_table)
The augmented probability table gives the probability of the next interval where the first and last values are the probabilities of going up or down an octave and the 12th value is the probability of no change.

Algorithm for determining note and rest start and length
Assume current note location is <current_location> where the start of the first beat has value 0 and this increases by one for each beat division. The location of the start of the next bar is divisions_per_beat * beats_per_bar.
If beats_per_cycle is divisions_per_beat * beats_per_bar *bars_per_cycle, then current_cycle_location is mod(current_location, beats_per_cycle).
The first step is to determine whether this will be a note or rest as determined by note_probability_table[current_cycle_location].
If it is a rest, then the length of the rest is determined by the probability distribution at rest_probability_table[current_cycle_location]. Where the first element is the probability of a length of one division and the last location is the probability of a length of one bar, ie., divisions_per_bar -1.
If it is a note, then the length of the rest is determined by the probability distribution at note_probability_table[current_cycle_location]. Where the first element is the probability of a length of one division and the last location is the probability of a length of one bar, ie., divisions_per_bar -1.
The current location counter is then incremented by either the note or rest duration, and the process is iterated.
Note volume.
Note volume is determined by min(1,max(0,location_volume[current_cycle_location] * pitch_volume[mod[pitch_current,12]*(1 + volume_variability * normal_distribution(0,1))

Output format.
I would like all of the tracks so produced to be output as individual tracks in a single midi file.  
