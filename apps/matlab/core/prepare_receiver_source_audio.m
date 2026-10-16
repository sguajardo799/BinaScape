function [audio, policy] = prepare_receiver_source_audio(audio, gain_db, downmix)
    original_channels = size(audio.timeData, 2);
    policy = struct('original_num_channels', original_channels, 'downmix_policy', 'none');
    if downmix && original_channels > 1
        audio.timeData = mean(audio.timeData, 2);
        policy.downmix_policy = 'arithmetic_mean';
    end
    audio.timeData = normalize_audio_peak(audio.timeData, 0.99) * 10^(gain_db / 20);
end
