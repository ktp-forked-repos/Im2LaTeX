import tensorflow as tf
import cPickle as cp
import numpy as np
from PIL import Image
import tempfile
import os
import pdb
import sys

import conv_net

#vocab_path = 'vocab.pkl'
#model_path = './models/'
#model_name = 'attn_cnn_gru_'
#train_list_file = '../data/im2latex_train.lst'
#train_png_folder = '../padded_formula_images/'
#formulas_file = '../data/im2latex_formulas.norm.lst'

vocab_path = '/home/ee/btech/ee1130504/ELL881/p/data/vocab.pkl'
model_path = '/scratch/ee/btech/ee1130504/p_ell881/models/'
model_name = 'cnn3_gru_lr_1e-3_e200_mem1000'
train_list_file = '/home/ee/btech/ee1130504/ELL881/p/data/im2latex_train.lst'
train_png_folder = '/home/ee/btech/ee1130504/ELL881/p/data/formula_images/'
formulas_file = '/home/ee/btech/ee1130504/ELL881/p/data/im2latex_formulas.norm.lst'

with open(vocab_path) as f:
    vocab = cp.load(f)

sess = tf.InteractiveSession()

# Parameters
learning_rate = 0.001
training_epochs = 10
batch_size = 128

# Network Parameters
IMG_HEIGHT = 160
IMG_WIDTH = 500
max_seq_length = 150
n_input = IMG_HEIGHT * IMG_WIDTH # im2latex data input (img shape: 160*500)
dropout = 0.75 # Dropout, probability to keep units
rnn_memory_dim = 1000
embedding_dim = 200
vocab_size = len(vocab)
train_now = True

print('Vocab size : ' + str(len(vocab)))

# tf Graph input
x_input = tf.placeholder(tf.float32, [None, n_input])
keep_prob = tf.placeholder(tf.float32) # dropout (keep probability)

# for RNN
pred = False if train_now else True
labels = [tf.placeholder(tf.int32, shape=(None,), name="labels%i"%t) for t in range(max_seq_length)]
weights = [tf.ones_like(labels_t, dtype=tf.float32) for labels_t in labels]
dec_inp = [tf.zeros_like(labels[0], dtype=np.int32, name="START")] + labels[:-1]    # ensure <S> token is 0th in vocab
prev_mem = tf.zeros((batch_size, rnn_memory_dim))

# conv_net weights, biases
conv_weights = {
    # 5x5 conv, 1 input, 32 outputs
    'wc1': tf.Variable(tf.random_normal([5, 5, 1, 32])),
    # 5x5 conv, 32 inputs, 64 outputs
    'wc2': tf.Variable(tf.random_normal([5, 5, 32, 64])),
    # 5x5 conv, 64 inputs, 128 outputs
    'wc3': tf.Variable(tf.random_normal([5, 5, 64, 128])),
    # 5x5 conv, 64 inputs, 256 outputs
    # 'wc4': tf.Variable(tf.random_normal([5, 5, 128, 256])),
    # fully connected, (IMG_HEIGHT/2/2/2)*(IMG_WIDTH/2/2/2)*256 inputs, 1024 outputs
    # TODO: +1 has been added to width in this case! (generalise!)
    'wd1': tf.Variable(tf.random_normal([(IMG_HEIGHT/2/2/2)*(IMG_WIDTH/2/2/2 + 1)*128, 1024])),
    # 1024 inputs, 10 outputs (class prediction)
    'out': tf.Variable(tf.random_normal([1024, rnn_memory_dim]))
}

conv_biases = {
    'bc1': tf.Variable(tf.random_normal([32])),
    'bc2': tf.Variable(tf.random_normal([64])),
    'bc3': tf.Variable(tf.random_normal([128])),
    #'bc4': tf.Variable(tf.random_normal([256])),
    'bd1': tf.Variable(tf.random_normal([1024])),
    'out': tf.Variable(tf.random_normal([rnn_memory_dim]))
}

# encoder decoder with attention
_, cnn_fc = conv_net.conv_net3(x_input, IMG_HEIGHT, IMG_WIDTH, conv_weights, conv_biases, dropout)
cnn_fc = tf.nn.tanh(cnn_fc)     # so, so important
cell = tf.nn.rnn_cell.GRUCell(rnn_memory_dim)
cell = tf.nn.rnn_cell.OutputProjectionWrapper(cell, vocab_size)
dec_outputs, dec_memory = tf.nn.seq2seq.embedding_rnn_decoder(dec_inp, cnn_fc, cell, vocab_size, embedding_dim, feed_previous=pred)

# loss
loss = tf.nn.seq2seq.sequence_loss(dec_outputs, labels, weights, vocab_size)
tf.scalar_summary("loss", loss)
summary_op = tf.merge_all_summaries()

# optimizer
optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
train_op = optimizer.minimize(loss)

# evaluate
pred = tf.to_int32(tf.argmax(dec_outputs, 2))    # max_seq_length * batch_size
all_labels = tf.reshape(labels, [max_seq_length, -1])
matches = tf.equal(pred, all_labels)
reduce_column_matches = tf.cast(tf.cast(tf.reduce_all(matches, 0), tf.int32), tf.float32)   # boolean to int to float
accuracy = tf.reduce_mean(reduce_column_matches) # correct only if perfectly correct

logdir = tempfile.mkdtemp()
print logdir
summary_writer = tf.train.SummaryWriter(logdir, sess.graph_def)

sess.run(tf.initialize_all_variables())

# DATA
with open(formulas_file) as f:
    formulas = f.read().splitlines()
with open(train_list_file) as f:
    train_list = [(x.split(' ')[1],int(x.split(' ')[0])) for x in f.read().splitlines()]	# (filename, formula_id)


def gen_next_batch(start_ind):
    # may return batch smaller than batch size in rare end cases
    c = 0
    cur_ind = start_ind
    dat_x = []
    dat_y = []
    # we might skip over some images in train_list either because they are too big and not present in
    # the png folder, or their strings are too long
    while cur_ind < len(train_list) and c < batch_size:
        if (not os.path.isfile(train_png_folder + train_list[cur_ind][0] + '.png')) or len(formulas[train_list[cur_ind][1]].split(' ')) > max_seq_length:
            # skip this one
            cur_ind += 1
            continue
        # add this
        img = Image.open(train_png_folder + train_list[cur_ind][0] + '.png')
        dat_x.append(np.asarray(img.convert('L')).flatten())	# flatten to give in as row vector

        cur_y = []
        for token in formulas[train_list[cur_ind][1]].split(' '):
            if token in vocab:
                cur_y.append(vocab[token])
            else:
                cur_y.append(vocab['<UNK>'])

        dat_y.append(cur_y + [vocab['</S>']]*(max_seq_length-len(cur_y)))
        c += 1
        cur_ind += 1

    return cur_ind, dat_x, dat_y


def train_batch(dat_x, dat_y):
    X = np.array(dat_x)	# batch_size * n_input
    Y = np.array(dat_y).T # max_seq_length * batch_size

    feed_dict = {x_input: X}
    feed_dict.update({keep_prob:dropout})
    feed_dict.update({labels[t]: Y[t] for t in range(max_seq_length)})

    _, loss_t, acc_t, summary = sess.run([train_op, loss, accuracy, summary_op], feed_dict)
    return loss_t, acc_t, summary

saver = tf.train.Saver()

# Training
print("Starting training!")
for epoch in range(training_epochs):
    avg_loss = 0.
    tot_cor = 0
    total_batch = 0
    i = 0
    # Loop over all batches
    while i + batch_size < len(train_list):
        i, dat_x, dat_y = gen_next_batch(i)
        loss_t, acc_t, summary = train_batch(dat_x, dat_y)
        summary_writer.add_summary(summary, t)
        avg_loss += loss_t
        tot_cor += acc_t * len(dat_x)
        total_batch += 1
        print("Batch " + str(total_batch) + " loss = " + "{:.5f}".format(loss_t))
        print("Batch " + str(total_batch) + " acc  = " + "{:.5f}".format(acc_t))
        sys.stdout.flush()

    # Display after each epoch
    print("Epoch : " + '%03d' % (epoch + 1) + " loss = " + "{:.9f}".format(avg_loss / total_batch))
    print("Epoch : " + '%03d' % (epoch + 1) + " acc  = " + "{:.9f}".format(tot_cor / batch_size*total_batch))
    sys.stdout.flush()
    save_path = saver.save(sess, model_path + model_name + '_' + str(epoch) + '_' + "{:.9f}".format(avg_loss/total_batch) + '.ckpt')

print("Optimization done!")

# pdb.set_trace()
